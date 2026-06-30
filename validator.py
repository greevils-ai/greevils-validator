"""Greevils subnet validator.

Runs an evaluation round daily at 00:00 UTC (the day boundary). Each round verifies
miners' on-chain Hyperliquid ownership claims, classifies each account as a greevil agent or
a human via greevils-api, scores EVERYONE in one unified tournament (agents and humans play
each other) with calculate_rewards, splits emissions into an agent pool and a human pool by
that score (the human pool capped in dollar terms, agents absorbing the rest), burns whatever
is unawarded to UID 0, and sets weights on-chain.

The one external dependency is greevils-api (valid-agent +
approval lookups). Until it is reachable, every account is scored as a human (the agent lane burns).

  python validator.py --network finney --netuid 1 --coldkey my-wallet --hotkey my-hotkey
  python validator.py --once         # run a single round and exit (handy for testing)
"""
import datetime as dt
import logging
import os
import sys
import threading
import time

import bittensor as bt
import click

from greevils_validator.config import GREEVILS_API, HEARTBEAT_TIMEOUT


def _seconds_until_next_utc_midnight() -> float:
    """Seconds from now until the next 00:00 UTC -- so the daily round (and its DB
    snapshot) fires at the UTC day boundary, making each recorded day's equity exact."""
    now = dt.datetime.now(dt.timezone.utc)
    nxt = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (nxt - now).total_seconds())
from greevils_validator.evaluation import run_evaluation_round

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def heartbeat_monitor(last_heartbeat, stop_event):
    """Re-exec the process if the main loop stops checking in (a hung chain/API call)."""
    while not stop_event.is_set():
        time.sleep(5)
        if time.time() - last_heartbeat[0] > HEARTBEAT_TIMEOUT:
            logger.error("No heartbeat in %ds. Restarting process.", HEARTBEAT_TIMEOUT)
            logging.shutdown()
            os.execv(sys.executable, [sys.executable] + sys.argv)


def _sleep_with_heartbeat(seconds, last_heartbeat, stop_event):
    """Sleep up to `seconds`, refreshing the heartbeat so the watchdog doesn't fire.

    The eval interval (24h) is far longer than HEARTBEAT_TIMEOUT (10m), so we can't just
    time.sleep -- we wake every 30s to mark ourselves alive.
    """
    deadline = time.time() + seconds
    while time.time() < deadline and not stop_event.is_set():
        last_heartbeat[0] = time.time()
        time.sleep(min(30, max(0, deadline - time.time())))


def set_weights(subtensor, wallet, netuid, uids, weights):
    """Push the round's (uids, weights) on-chain. Floats are normalized by the SDK."""
    logger.info("Setting weights: %s", {u: round(w, 4) for u, w in zip(uids, weights)})
    resp = subtensor.set_weights(
        wallet=wallet,
        netuid=netuid,
        uids=uids,
        weights=weights,
        wait_for_inclusion=True,
        wait_for_finalization=False,
    )
    # set_weights returns an ExtrinsicResponse (no __bool__, so `if resp` is always truthy);
    # unpack it -- it yields (success, message), which also matches the older (bool, str) return.
    success, message = resp
    if success:
        logger.info("Weights set for %d UIDs", len(uids))
    else:
        logger.warning("Failed to set weights: %s", message)
    return success


@click.command()
@click.option("--network", default=lambda: os.getenv("NETWORK", "finney"),
              help="Network to connect to (finney, test, local)")
@click.option("--netuid", type=int, default=lambda: int(os.getenv("NETUID", "1")),
              help="Subnet netuid")
@click.option("--coldkey", default=lambda: os.getenv("WALLET_NAME", "default"), help="Wallet name")
@click.option("--hotkey", default=lambda: os.getenv("HOTKEY_NAME", "default"), help="Hotkey name")
@click.option("--api", default=lambda: GREEVILS_API, help="greevils-api base URL")
@click.option("--once", is_flag=True, help="Run a single evaluation round and exit")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
              default=lambda: os.getenv("LOG_LEVEL", "INFO"), help="Logging level")
def main(network, netuid, coldkey, hotkey, api, once, log_level):
    """Run the Greevils subnet validator."""
    logging.getLogger().setLevel(getattr(logging, log_level.upper()))
    logger.info("Starting validator on network=%s, netuid=%s, api=%s", network, netuid, api)

    # Warn if the builder-exclusivity start date is recent enough to exempt real history.
    from greevils_validator.config import BUILDER_EXCLUSIVITY_START
    if BUILDER_EXCLUSIVITY_START and (dt.datetime.now(dt.timezone.utc).date() - BUILDER_EXCLUSIVITY_START).days < 30:
        logger.warning("BUILDER_EXCLUSIVITY_START=%s is recent -- fills before it are EXEMPT from the "
                       "builder-exclusivity check; set it to the rule's real launch date.",
                       BUILDER_EXCLUSIVITY_START)

    last_heartbeat = [time.time()]
    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=heartbeat_monitor, args=(last_heartbeat, stop_event), daemon=True
    )
    heartbeat_thread.start()

    try:
        wallet = bt.Wallet(name=coldkey, hotkey=hotkey)
        subtensor = bt.Subtensor(network=network)
        metagraph = bt.Metagraph(netuid=netuid, network=network)
        metagraph.sync(subtensor=subtensor)
        logger.info("Metagraph synced: %d neurons at block %d", metagraph.n, metagraph.block)

        my_hotkey = wallet.hotkey.ss58_address
        if my_hotkey not in metagraph.hotkeys:
            logger.error("Hotkey %s not registered on netuid %d", my_hotkey, netuid)
            return
        logger.info("Validator UID: %d", metagraph.hotkeys.index(my_hotkey))

        # ALLOWED_PAIRS is the DEFINITIVE curated whitelist -- ONLY those pairs are tradable;
        # we do NOT mirror all of HL (a miner can functionally trade any HL market through the
        # builder app, but trading one outside this list eliminates them). Best-effort startup
        # sanity check: warn about any curated symbol that matches no live HL market (a typo /
        # delisted / renamed pair would silently and permanently false-DQ honest miners).
        try:
            from greevils_validator import scoring
            from greevils_validator.hl_data import fetch_allowed_perps
            live = fetch_allowed_perps()
            dead = sorted(p for p in scoring.ALLOWED_PAIRS if scoring.normalize_pair(p) not in live)
            if dead:
                logger.warning("ALLOWED_PAIRS has %d symbol(s) matching NO live HL market "
                               "(typo/delisted -> would false-DQ): %s", len(dead), dead)
            else:
                logger.info("ALLOWED_PAIRS: all %d curated pairs match live HL markets",
                            len(scoring.ALLOWED_PAIRS))
        except Exception as e:  # noqa: BLE001
            logger.warning("could not validate ALLOWED_PAIRS against HL (%s)", e)

        while True:
            last_heartbeat[0] = time.time()
            try:
                metagraph.sync(subtensor=subtensor)
                uids, weights = run_evaluation_round(subtensor, metagraph, netuid, api)
                set_weights(subtensor, wallet, netuid, uids, weights)
            except KeyboardInterrupt:
                logger.info("Validator stopped by user")
                break
            except Exception as e:  # noqa: BLE001 -- one bad round shouldn't kill the validator
                logger.exception("Error in evaluation round: %s", e)

            if once:
                break
            # First round at boot may run off-boundary; every round after lands at ~00:00 UTC.
            secs = _seconds_until_next_utc_midnight()
            logger.info("Next evaluation round at 00:00 UTC (in %d s)", secs)
            _sleep_with_heartbeat(secs, last_heartbeat, stop_event)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


if __name__ == "__main__":
    main()
