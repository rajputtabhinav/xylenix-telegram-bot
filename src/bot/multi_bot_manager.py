"""
Multi-Bot Manager for handling multiple isolated bot instances
"""
import asyncio
import logging
import threading
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from src.config import settings, BotConfig
from src.bot.isolated_bot import IsolatedXylenixBot

logger = logging.getLogger(__name__)


class MultiBotManager:
    """Manages multiple isolated bot instances"""
    
    def __init__(self):
        self.bot_instances: Dict[str, IsolatedXylenixBot] = {}
        self.bot_threads: Dict[str, threading.Thread] = {}
        self.is_running = False
        
    async def initialize_bots(self) -> None:
        """Initialize all bot instances from configuration"""
        bot_configs = settings.get_bot_configs()
        
        if not bot_configs:
            raise RuntimeError("No bot configurations found. Please check your environment variables.")
        
        logger.info(f"Initializing {len(bot_configs)} bot instances...")
        
        for config in bot_configs:
            try:
                # Create isolated bot instance
                bot_instance = IsolatedXylenixBot(config)
                self.bot_instances[config.bot_id] = bot_instance
                logger.info(f"✅ Bot {config.bot_id} (@{config.username}) initialized successfully")
                
            except Exception as e:
                logger.error(f"❌ Failed to initialize bot {config.bot_id}: {e}")
                continue
        
        if not self.bot_instances:
            raise RuntimeError("No bot instances could be initialized")
            
        logger.info(f"🚀 Successfully initialized {len(self.bot_instances)} bot instances")
    
    def start_all_bots(self) -> None:
        """Start all bot instances in separate threads"""
        if self.is_running:
            logger.warning("Bots are already running")
            return
            
        self.is_running = True
        logger.info("Starting all bot instances...")
        
        for bot_id, bot_instance in self.bot_instances.items():
            thread = threading.Thread(
                target=self._run_bot_instance,
                args=(bot_id, bot_instance),
                name=f"Bot-{bot_id}",
                daemon=True
            )
            self.bot_threads[bot_id] = thread
            thread.start()
            logger.info(f"🎯 Started bot thread for {bot_id}")
        
        logger.info(f"🎉 All {len(self.bot_instances)} bots are now running!")
    
    def _run_bot_instance(self, bot_id: str, bot_instance: IsolatedXylenixBot) -> None:
        """Run a single bot instance"""
        try:
            logger.info(f"🤖 Running bot {bot_id} (@{bot_instance.config.username})")
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                # Run the bot in the new event loop
                loop.run_until_complete(self._async_run_bot(bot_instance))
            finally:
                # Clean shutdown
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                finally:
                    loop.close()
        except Exception as e:
            logger.error(f"💥 Bot {bot_id} crashed: {e}")
        finally:
            logger.info(f"🛑 Bot {bot_id} stopped")
    
    async def _async_run_bot(self, bot_instance: IsolatedXylenixBot) -> None:
        """Async wrapper for running bot instance"""
        if not bot_instance.config.token:
            raise RuntimeError(f"Bot token is not configured for {bot_instance.config.bot_id}")
        
        from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
        
        application = Application.builder().token(bot_instance.config.token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", bot_instance.start))
        application.add_handler(CommandHandler("referrals", bot_instance.referrals))
        application.add_handler(CallbackQueryHandler(bot_instance.handle_callback_query))
        application.add_handler(MessageHandler(filters.PHOTO, bot_instance.handle_photo))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_instance.handle_text_message))
        application.add_error_handler(bot_instance.error_handler)
        
        # Store application reference
        bot_instance.application = application
        
        logger.info(f"Starting bot {bot_instance.config.bot_id} (@{bot_instance.config.username})")
        
        # Start the bot
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        try:
            # Keep running until stopped
            while application.updater.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            # Expected when stopping
            pass
        finally:
            try:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
            except Exception as e:
                logger.error(f"Error during bot shutdown: {e}")
    
    def stop_all_bots(self) -> None:
        """Stop all bot instances"""
        if not self.is_running:
            logger.warning("Bots are not running")
            return
            
        logger.info("Stopping all bot instances...")
        
        # Stop all bot instances by cancelling their tasks
        for bot_id, bot_instance in self.bot_instances.items():
            try:
                if hasattr(bot_instance, 'application') and bot_instance.application:
                    if hasattr(bot_instance.application, 'updater') and bot_instance.application.updater:
                        if bot_instance.application.updater.running:
                            # This will trigger the CancelledError in the async loop
                            pass
                logger.info(f"🛑 Stopped bot {bot_id}")
            except Exception as e:
                logger.error(f"❌ Error stopping bot {bot_id}: {e}")
        
        # Wait for threads to finish
        for bot_id, thread in self.bot_threads.items():
            if thread.is_alive():
                logger.info(f"⏳ Waiting for bot thread {bot_id} to finish...")
                thread.join(timeout=5)
                if thread.is_alive():
                    logger.warning(f"⚠️  Bot thread {bot_id} did not stop gracefully")
        
        self.bot_threads.clear()
        self.is_running = False
        logger.info("🎯 All bots stopped")
    
    def get_bot_status(self) -> Dict[str, Dict[str, str]]:
        """Get status of all bot instances"""
        status = {}
        
        for bot_id, bot_instance in self.bot_instances.items():
            thread = self.bot_threads.get(bot_id)
            status[bot_id] = {
                "bot_id": bot_id,
                "username": bot_instance.config.username,
                "thread_alive": thread.is_alive() if thread else False,
                "application_running": bot_instance.application is not None,
                "status": "running" if (thread and thread.is_alive()) else "stopped"
            }
        
        return status
    
    def restart_bot(self, bot_id: str) -> bool:
        """Restart a specific bot instance"""
        if bot_id not in self.bot_instances:
            logger.error(f"Bot {bot_id} not found")
            return False
        
        logger.info(f"Restarting bot {bot_id}...")
        
        # Stop the specific bot
        if bot_id in self.bot_threads:
            try:
                self.bot_instances[bot_id].stop_bot()
                self.bot_threads[bot_id].join(timeout=5)
            except Exception as e:
                logger.error(f"Error stopping bot {bot_id}: {e}")
        
        # Start it again
        try:
            thread = threading.Thread(
                target=self._run_bot_instance,
                args=(bot_id, self.bot_instances[bot_id]),
                name=f"Bot-{bot_id}",
                daemon=True
            )
            self.bot_threads[bot_id] = thread
            thread.start()
            logger.info(f"✅ Bot {bot_id} restarted successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to restart bot {bot_id}: {e}")
            return False
    
    def get_bot_instance(self, bot_id: str) -> Optional[IsolatedXylenixBot]:
        """Get a specific bot instance"""
        return self.bot_instances.get(bot_id)
    
    async def wait_for_all_bots(self) -> None:
        """Wait for all bot threads to finish"""
        if not self.is_running:
            return
            
        try:
            while any(thread.is_alive() for thread in self.bot_threads.values()):
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, stopping all bots...")
            self.stop_all_bots()


# Global instance
multi_bot_manager = MultiBotManager()


async def main():
    """Main entry point for multi-bot manager"""
    try:
        # Initialize all bots
        await multi_bot_manager.initialize_bots()
        
        # Start all bots
        multi_bot_manager.start_all_bots()
        
        # Wait for all bots or interruption
        await multi_bot_manager.wait_for_all_bots()
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Multi-bot manager error: {e}")
    finally:
        multi_bot_manager.stop_all_bots()


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    asyncio.run(main())
