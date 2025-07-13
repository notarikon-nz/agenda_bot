#!/usr/bin/env python3
"""
TTS Donation Queue System
=========================

A robust donation queue system that integrates with OBS and Stream Deck.

Features:
- Queue management with persistent storage
- TTS integration
- OBS text source updates
- Discord webhook integration
- Stream Deck button handling
- Error recovery and logging

Requirements:
- pip install obswebsocket-python discord-webhook gtts pygame flask requests
- OBS with websocket plugin enabled
- Stream Deck with HTTP request capability

Author: Assistant
License: MIT
"""

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import queue
import tempfile

import obswebsocket # type: ignore
import requests
from discord_webhook import DiscordWebhook # type: ignore
from flask import Flask, request, jsonify # type: ignore
from gtts import gTTS # type: ignore
import pygame # type: ignore

# Configure logging for debugging and monitoring
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tts_queue.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Config:
    """Configuration management with validation and defaults"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.default_config = {
            "obs": {
                "host": "localhost",
                "port": 4455,
                "password": "",
                "queue_scene": "Stream",
                "queue_text_source": "TTS Queue Counter"
            },
            "discord": {
                "webhook_url": "",
                "enabled": True
            },
            "tts": {
                "language": "en",
                "slow": False,
                "volume": 0.8
            },
            "server": {
                "host": "localhost",
                "port": 5000
            },
            "database": {
                "path": "tts_queue.db"
            }
        }
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """Load configuration from file or create default"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                # Merge with defaults to handle missing keys
                merged_config = self.default_config.copy()
                self._deep_merge(merged_config, config)
                return merged_config
            else:
                logger.info(f"Config file not found, creating default: {self.config_file}")
                self.save_config(self.default_config)
                return self.default_config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return self.default_config
    
    def save_config(self, config: Dict):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def _deep_merge(self, base: Dict, update: Dict):
        """Deep merge two dictionaries"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def get(self, key_path: str, default=None):
        """Get configuration value using dot notation (e.g., 'obs.host')"""
        keys = key_path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

class DatabaseManager:
    """Database operations for persistent queue storage"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS queue_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        message TEXT NOT NULL,
                        amount REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        processed BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS queue_stats (
                        id INTEGER PRIMARY KEY,
                        total_processed INTEGER DEFAULT 0,
                        total_amount REAL DEFAULT 0.0,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Initialize stats if empty
                cursor = conn.execute('SELECT COUNT(*) FROM queue_stats')
                if cursor.fetchone()[0] == 0:
                    conn.execute('INSERT INTO queue_stats (id) VALUES (1)')
                conn.commit()
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
    
    def add_to_queue(self, username: str, message: str, amount: float) -> bool:
        """Add item to queue"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO queue_items (username, message, amount, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (username, message, amount, datetime.now().isoformat()))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding to queue: {e}")
            return False
    
    def get_next_item(self) -> Optional[Tuple]:
        """Get next unprocessed item from queue"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT id, username, message, amount, timestamp
                    FROM queue_items
                    WHERE processed = FALSE
                    ORDER BY created_at ASC
                    LIMIT 1
                ''')
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"Error getting next item: {e}")
            return None
    
    def mark_processed(self, item_id: int) -> bool:
        """Mark item as processed"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    UPDATE queue_items SET processed = TRUE WHERE id = ?
                ''', (item_id,))
                conn.execute('''
                    UPDATE queue_stats
                    SET total_processed = total_processed + 1,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE id = 1
                ''')
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error marking processed: {e}")
            return False
    
    def get_queue_stats(self) -> Dict:
        """Get queue statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT
                        COUNT(*) as total_in_queue,
                        COALESCE(SUM(amount), 0) as total_amount_pending
                    FROM queue_items
                    WHERE processed = FALSE
                ''')
                pending_stats = cursor.fetchone()
                
                cursor = conn.execute('''
                    SELECT total_processed, total_amount
                    FROM queue_stats
                    WHERE id = 1
                ''')
                processed_stats = cursor.fetchone()
                
                return {
                    'total_in_queue': pending_stats[0],
                    'total_amount_pending': pending_stats[1],
                    'total_processed': processed_stats[0] if processed_stats else 0,
                    'total_amount_processed': processed_stats[1] if processed_stats else 0.0
                }
        except Exception as e:
            logger.error(f"Error getting queue stats: {e}")
            return {'total_in_queue': 0, 'total_amount_pending': 0.0, 'total_processed': 0, 'total_amount_processed': 0.0}

class TTSManager:
    """Text-to-Speech management with audio playback"""
    
    def __init__(self, config: Config):
        self.config = config
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.init_audio()
    
    def init_audio(self):
        """Initialize pygame mixer for audio playback"""
        try:
            pygame.mixer.init()
            logger.info("Audio system initialized")
        except Exception as e:
            logger.error(f"Audio initialization error: {e}")
    
    def generate_and_play_tts(self, text: str, username: str) -> bool:
        """Generate TTS and play audio"""
        try:
            # Prepare TTS text
            tts_text = f"{username} said {text}"
            
            # Generate TTS
            tts = gTTS(
                text=tts_text,
                lang=self.config.get('tts.language', 'en'),
                slow=self.config.get('tts.slow', False)
            )
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                tts.save(tmp_file.name)
                
                # Play audio
                pygame.mixer.music.load(tmp_file.name)
                pygame.mixer.music.set_volume(self.config.get('tts.volume', 0.8))
                pygame.mixer.music.play()
                
                # Wait for playback to finish
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
                
                # Clean up temporary file
                os.unlink(tmp_file.name)
                
                logger.info(f"TTS played successfully for {username}")
                return True
                
        except Exception as e:
            logger.error(f"TTS generation/playback error: {e}")
            return False

class OBSManager:
    """OBS integration for updating text sources"""
    
    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.connected = False
        self.connect()
    
    def connect(self):
        """Connect to OBS WebSocket"""
        try:
            self.client = obswebsocket.obsws(
                host=self.config.get('obs.host', 'localhost'),
                port=self.config.get('obs.port', 4455),
                password=self.config.get('obs.password', '')
            )
            self.client.connect()
            self.connected = True
            logger.info("Connected to OBS")
        except Exception as e:
            logger.error(f"OBS connection error: {e}")
            self.connected = False
    
    def update_queue_display(self, stats: Dict):
        """Update OBS text source with queue information"""
        if not self.connected:
            self.connect()
        
        if not self.connected:
            return False
        
        try:
            text_content = f"Queue: {stats['total_processed']}/{stats['total_processed'] + stats['total_in_queue']}"
            
            self.client.call(obswebsocket.requests.SetTextGDIPlusText(
                source=self.config.get('obs.queue_text_source', 'TTS Queue Counter'),
                text=text_content
            ))
            
            logger.info(f"Updated OBS display: {text_content}")
            return True
            
        except Exception as e:
            logger.error(f"OBS update error: {e}")
            self.connected = False
            return False

class DiscordManager:
    """Discord webhook integration"""
    
    def __init__(self, config: Config):
        self.config = config
        self.webhook_url = config.get('discord.webhook_url', '')
        self.enabled = config.get('discord.enabled', True) and bool(self.webhook_url)
    
    def send_message(self, username: str, message: str, amount: float, timestamp: str) -> bool:
        """Send message to Discord"""
        if not self.enabled:
            return True
        
        try:
            webhook = DiscordWebhook(url=self.webhook_url)
            
            embed_content = f"**{username}** donated ${amount:.2f}\n\n*{message}*\n\n`{timestamp}`"
            webhook.content = embed_content
            
            response = webhook.execute()
            
            if response.status_code == 200:
                logger.info(f"Discord message sent for {username}")
                return True
            else:
                logger.error(f"Discord webhook error: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Discord send error: {e}")
            return False

class TTSQueueSystem:
    """Main TTS Queue System orchestrator"""
    
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager(self.config.get('database.path', 'tts_queue.db'))
        self.tts = TTSManager(self.config)
        self.obs = OBSManager(self.config)
        self.discord = DiscordManager(self.config)
        self.processing_lock = threading.Lock()
        
        # Initialize Flask app for Stream Deck integration
        self.app = Flask(__name__)
        self.setup_routes()
        
        logger.info("TTS Queue System initialized")
    
    def setup_routes(self):
        """Setup Flask routes for Stream Deck integration"""
        
        @self.app.route('/add_donation', methods=['POST'])
        def add_donation():
            """Add donation to queue via HTTP POST"""
            try:
                data = request.json
                username = data.get('username', 'Anonymous')
                message = data.get('message', '')
                amount = float(data.get('amount', 0.0))
                
                if self.add_to_queue(username, message, amount):
                    return jsonify({'status': 'success', 'message': 'Added to queue'})
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to add to queue'}), 500
                    
            except Exception as e:
                logger.error(f"Add donation error: {e}")
                return jsonify({'status': 'error', 'message': str(e)}), 500
        
        @self.app.route('/process_next', methods=['POST'])
        def process_next():
            """Process next item in queue (Stream Deck button)"""
            try:
                if self.process_next_item():
                    return jsonify({'status': 'success', 'message': 'Item processed'})
                else:
                    return jsonify({'status': 'error', 'message': 'No items in queue or processing failed'})
                    
            except Exception as e:
                logger.error(f"Process next error: {e}")
                return jsonify({'status': 'error', 'message': str(e)}), 500
        
        @self.app.route('/queue_stats', methods=['GET'])
        def queue_stats():
            """Get queue statistics"""
            try:
                stats = self.db.get_queue_stats()
                return jsonify(stats)
            except Exception as e:
                logger.error(f"Queue stats error: {e}")
                return jsonify({'error': str(e)}), 500
    
    def add_to_queue(self, username: str, message: str, amount: float) -> bool:
        """Add item to queue and update displays"""
        try:
            if self.db.add_to_queue(username, message, amount):
                self.update_displays()
                logger.info(f"Added to queue: {username} - ${amount:.2f}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error adding to queue: {e}")
            return False
    
    def process_next_item(self) -> bool:
        """Process next item in queue"""
        with self.processing_lock:
            try:
                item = self.db.get_next_item()
                if not item:
                    logger.info("No items in queue to process")
                    return False
                
                item_id, username, message, amount, timestamp = item
                
                # Play TTS
                if self.tts.generate_and_play_tts(message, username):
                    # Mark as processed
                    self.db.mark_processed(item_id)
                    
                    # Send to Discord
                    self.discord.send_message(username, message, amount, timestamp)
                    
                    # Update displays
                    self.update_displays()
                    
                    logger.info(f"Processed: {username} - ${amount:.2f}")
                    return True
                else:
                    logger.error("TTS playback failed")
                    return False
                    
            except Exception as e:
                logger.error(f"Error processing item: {e}")
                return False
    
    def update_displays(self):
        """Update OBS and other displays"""
        try:
            stats = self.db.get_queue_stats()
            self.obs.update_queue_display(stats)
        except Exception as e:
            logger.error(f"Error updating displays: {e}")
    
    def run_server(self):
        """Run the Flask server"""
        try:
            self.app.run(
                host=self.config.get('server.host', 'localhost'),
                port=self.config.get('server.port', 5000),
                debug=False
            )
        except Exception as e:
            logger.error(f"Server error: {e}")

def main():
    """Main entry point"""
    try:
        system = TTSQueueSystem()
        
        # Update displays on startup
        system.update_displays()
        
        logger.info("Starting TTS Queue System server...")
        logger.info("Stream Deck endpoints:")
        logger.info("  - POST /process_next (for Stream Deck button)")
        logger.info("  - POST /add_donation (for adding donations)")
        logger.info("  - GET /queue_stats (for monitoring)")
        
        system.run_server()
        
    except KeyboardInterrupt:
        logger.info("Shutting down TTS Queue System...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()