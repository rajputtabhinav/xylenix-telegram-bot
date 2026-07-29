import asyncio
import logging
import sys
from typing import Optional

from src.config import settings
from src.bot.multi_bot_manager import multi_bot_manager

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def run_single_bot(bot_id: Optional[str] = None) -> None:
    """Run a single bot instance (for backward compatibility or testing)"""
    from src.bot.isolated_bot import IsolatedXylenixBot
    from src.config import BotConfig
    
    try:
        if bot_id:
            # Find specific bot config
            configs = settings.get_bot_configs()
            config = next((c for c in configs if c.bot_id == bot_id), None)
            if not config:
                raise ValueError(f"Bot configuration not found for {bot_id}")
        else:
            # Use default/first available config
            configs = settings.get_bot_configs()
            if not configs:
                raise ValueError("No bot configurations found")
            config = configs[0]
        
        logger.info(f"Running single bot: {config.bot_id} (@{config.username})")
        
        bot_instance = IsolatedXylenixBot(config)
        bot_instance.run_bot()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise


async def run_multi_bots() -> None:
    """Run all configured bots simultaneously"""
    try:
        logger.info("Starting Multi-Bot Manager...")
        
        # Initialize all bots
        await multi_bot_manager.initialize_bots()
        
        # Start all bots
        multi_bot_manager.start_all_bots()
        
        logger.info("All bots are running. Press Ctrl+C to stop.")
        
        # Wait for interruption or all bots to stop
        await multi_bot_manager.wait_for_all_bots()
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Multi-bot manager error: {e}")
        raise
    finally:
        multi_bot_manager.stop_all_bots()


def main() -> None:
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Xylenix Multi-Bot System')
    parser.add_argument('--single', type=str, help='Run single bot by bot_id')
    parser.add_argument('--multi', action='store_true', help='Run all bots simultaneously')
    
    args = parser.parse_args()
    
    try:
        if args.single:
            # Run single bot
            asyncio.run(run_single_bot(args.single))
        elif args.multi:
            # Run all bots
            asyncio.run(run_multi_bots())
        else:
            # Default: run all bots
            logger.info("No specific mode selected. Running all bots...")
            asyncio.run(run_multi_bots())
            
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()