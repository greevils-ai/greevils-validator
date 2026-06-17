"""Greevils subnet validator.

Runs an evaluation round every EVAL_INTERVAL seconds (default 24h). Each round verifies
miners' on-chain Hyperliquid ownership claims, classifies each account as a greevil agent or
a human via greevils-api, scores agents-vs-agents and humans-vs-humans with calculate_rewards,
splits emissions 90/10, burns any empty/loser arena to UID 0, and sets weights on-chain.

The only thing to implement is calculate_rewards in greevils_validator/rewards.py.

  python validator.py --network finney --netuid 1 --coldkey my-wallet --hotkey my-hotkey
  python validator.py --once         # run a single round and exit (handy for testing)
"""
import logging
import os
import sys
import threading
import time

import bittensor as bt
import click

from greevils_validator.config import EVAL_INTERVAL, GREEVILS_API, HEARTBEAT_TIMEOUT
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
    success = subtensor.set_weights(
        wallet=wallet,
        netuid=netuid,
        uids=uids,
        weights=weights,
        wait_for_inclusion=True,
        wait_for_finalization=False,
    )
    if success:
        logger.info("Weights set for %d UIDs", len(uids))
    else:
        logger.warning("Failed to set weights")
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
            logger.info("Next evaluation round in %d seconds", EVAL_INTERVAL)
            _sleep_with_heartbeat(EVAL_INTERVAL, last_heartbeat, stop_event)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


if __name__ == "__main__":
    main()
