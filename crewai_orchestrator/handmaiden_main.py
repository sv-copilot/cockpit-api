#!/usr/bin/env python3
"""
Entry point for the Handmaiden health check loop.
Usage:
    python3 handmaiden_main.py            # start poll loop
    python3 handmaiden_main.py --once     # run one sweep and exit
"""
import argparse
import logging
import sys

from tools.crewai_orchestrator.crews.handmaiden import HandmaidenCrew

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("handmaiden_main")


def main():
    parser = argparse.ArgumentParser(description="Handmaiden infrastructure health monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single health sweep and exit (for cron mode or testing)",
    )
    args = parser.parse_args()

    crew = HandmaidenCrew()

    if args.once:
        logger.info("Running single health sweep...")
        try:
            sweep = crew.run_single_sweep()
            crew.write_heartbeat(sweep)
            print(sweep.to_dict())  # output to stdout for external capture
            logger.info("Single sweep completed: %s", sweep.sweep_id)
        except Exception as e:
            logger.error("Single sweep failed: %s", str(e))
            sys.exit(1)
    else:
        crew.run_loop()


if __name__ == "__main__":
    main()