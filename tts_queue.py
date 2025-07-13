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

Author: Matt Orsborn
License: MIT
"""

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import queue
import tempfile
import hashlib
import aiohttp

import obswebsocket # type: ignore
import requests
from discord_webhook import DiscordWebhook # type: ignore
from flask import Flask, request, jsonify # type: ignore
from gtts import gTTS # type: ignore
import pygame # type: ignore
from concurrent.futures import ThreadPoolExecutor

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

class TTSProvider(ABC):
    """Abstract base class for TTS providers"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.name = self.__class__.__name__
        self.enabled = config.get('enabled', True)
        self.cache_dir = Path(config.get('cache_dir', 'tts_cache')) / self.name.lower()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
    @abstractmethod
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        """Generate speech and return path to audio file"""
        pass
    
    @abstractmethod
    def get_available_voices(self) -> List[Dict]:
        """Get list of available voices"""
        pass
    
    def get_cache_path(self, text: str, voice: str = None, **kwargs) -> Path:
        """Generate cache file path based on text and parameters"""
        cache_key = hashlib.md5(f"{text}_{voice}_{kwargs}".encode()).hexdigest()
        return self.cache_dir / f"{cache_key}.mp3"
    
    async def check_availability(self) -> bool:
        """Check if this TTS provider is available"""
        try:
            # Simple test generation
            test_file = await self.generate_speech("test", cache=False)
            if test_file and os.path.exists(test_file):
                os.unlink(test_file)
                return True
        except Exception as e:
            logger.debug(f"{self.name} availability check failed: {e}")
        return False

class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge TTS - Free and high quality"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        try:
            import edge_tts
            self.edge_tts = edge_tts
            self.available = True
        except ImportError:
            logger.warning("edge-tts not installed. Install with: pip install edge-tts")
            self.available = False
    
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        if not self.available:
            return None
            
        voice = voice or self.config.get('default_voice', 'en-US-AriaNeural')
        use_ssml = kwargs.get('use_ssml', self.config.get('use_ssml', False))
        rate = kwargs.get('rate', self.config.get('rate', '+0%'))
        pitch = kwargs.get('pitch', self.config.get('pitch', '+0Hz'))
        
        # Check cache first
        cache_enabled = kwargs.get('cache', True)
        cache_path = self.get_cache_path(text, voice, rate=rate, pitch=pitch, use_ssml=use_ssml)
        
        if cache_enabled and cache_path.exists():
            return str(cache_path)
        
        try:
            if use_ssml and (rate != '+0%' or pitch != '+0Hz'):
                # Use SSML only when we need rate/pitch control
                import html
                escaped_text = html.escape(text)
                
                ssml_text = f'''<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
                    <voice name="{voice}">
                        <prosody rate="{rate}" pitch="{pitch}">
                            {escaped_text}
                        </prosody>
                    </voice>
                </speak>'''
                
                communicate = self.edge_tts.Communicate(ssml_text, voice)
            else:
                # Simple text-only approach (recommended for most use cases)
                communicate = self.edge_tts.Communicate(text, voice)
            
            if cache_enabled:
                output_path = cache_path
            else:
                output_path = Path(tempfile.mktemp(suffix='.mp3'))
            
            await communicate.save(str(output_path))
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Edge TTS generation failed: {e}")
            # Always try simple fallback without SSML
            try:
                logger.info("Trying Edge TTS with simple text (no SSML)")
                communicate = self.edge_tts.Communicate(text, voice)
                
                if cache_enabled:
                    output_path = cache_path
                else:
                    output_path = Path(tempfile.mktemp(suffix='.mp3'))
                
                await communicate.save(str(output_path))
                return str(output_path)
                
            except Exception as e2:
                logger.error(f"Edge TTS simple fallback also failed: {e2}")
                return None
    
    def get_available_voices(self) -> List[Dict]:
        if not self.available:
            return []
        
        # Common high-quality Edge voices
        return [
            {"name": "en-US-AriaNeural", "language": "en-US", "gender": "Female", "description": "Aria - Natural female voice"},
            {"name": "en-US-JennyNeural", "language": "en-US", "gender": "Female", "description": "Jenny - Cheerful female voice"},
            {"name": "en-US-GuyNeural", "language": "en-US", "gender": "Male", "description": "Guy - Natural male voice"},
            {"name": "en-US-DavisNeural", "language": "en-US", "gender": "Male", "description": "Davis - Confident male voice"},
            {"name": "en-GB-SoniaNeural", "language": "en-GB", "gender": "Female", "description": "Sonia - British female voice"},
            {"name": "en-GB-RyanNeural", "language": "en-GB", "gender": "Male", "description": "Ryan - British male voice"},
            {"name": "en-AU-NatashaNeural", "language": "en-AU", "gender": "Female", "description": "Natasha - Australian female voice"},
            {"name": "en-AU-WilliamNeural", "language": "en-AU", "gender": "Male", "description": "William - Australian male voice"},
        ]

class GTTSProvider(TTSProvider):
    """Google TTS - Basic but reliable fallback"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        try:
            from gtts import gTTS # type: ignore
            self.gTTS = gTTS
            self.available = True
        except ImportError:
            logger.warning("gtts not installed. Install with: pip install gtts")
            self.available = False
    
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        if not self.available:
            return None
            
        lang = voice or self.config.get('language', 'en')
        slow = kwargs.get('slow', self.config.get('slow', False))
        
        cache_enabled = kwargs.get('cache', True)
        cache_path = self.get_cache_path(text, lang, slow=slow)
        
        if cache_enabled and cache_path.exists():
            return str(cache_path)
        
        try:
            tts = self.gTTS(text=text, lang=lang, slow=slow)
            
            if cache_enabled:
                output_path = cache_path
            else:
                output_path = Path(tempfile.mktemp(suffix='.mp3'))
            
            tts.save(str(output_path))
            return str(output_path)
            
        except Exception as e:
            logger.error(f"gTTS generation failed: {e}")
            return None
    
    def get_available_voices(self) -> List[Dict]:
        return [
            {"name": "en", "language": "en-US", "gender": "Neutral", "description": "English (US)"},
            {"name": "en-uk", "language": "en-GB", "gender": "Neutral", "description": "English (UK)"},
            {"name": "en-au", "language": "en-AU", "gender": "Neutral", "description": "English (AU)"},
        ]

class CoquiTTSProvider(TTSProvider):
    """Coqui TTS - Local processing, good quality"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        try:
            import TTS  # type: ignore
            from TTS.api import TTS as CoquiTTS # type: ignore
            self.TTS = CoquiTTS
            self.model_name = config.get('model', 'tts_models/en/ljspeech/tacotron2-DDC')
            self.tts_model = None
            self.available = True
        except ImportError:
            logger.warning("TTS (Coqui) not installed. Install with: pip install TTS")
            self.available = False
    
    def _load_model(self):
        """Lazy load the TTS model"""
        if self.tts_model is None and self.available:
            try:
                self.tts_model = self.TTS(self.model_name)
            except Exception as e:
                logger.error(f"Failed to load Coqui TTS model: {e}")
                self.available = False
    
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        if not self.available:
            return None
        
        self._load_model()
        if not self.tts_model:
            return None
        
        cache_enabled = kwargs.get('cache', True)
        cache_path = self.get_cache_path(text, voice or 'default')
        
        if cache_enabled and cache_path.exists():
            return str(cache_path)
        
        try:
            if cache_enabled:
                output_path = cache_path
            else:
                output_path = Path(tempfile.mktemp(suffix='.wav'))
            
            self.tts_model.tts_to_file(text=text, file_path=str(output_path))
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Coqui TTS generation failed: {e}")
            return None
    
    def get_available_voices(self) -> List[Dict]:
        return [
            {"name": "default", "language": "en", "gender": "Neutral", "description": "Default Coqui voice"},
        ]

class AzureTTSProvider(TTSProvider):
    """Azure Cognitive Services TTS - Premium quality"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.region = config.get('region', 'eastus')
        self.available = bool(self.api_key)
        
        if not self.available:
            logger.warning("Azure TTS: API key not configured")
    
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        if not self.available:
            return None
            
        voice = voice or self.config.get('default_voice', 'en-US-AriaNeural')
        rate = kwargs.get('rate', self.config.get('rate', '1.0'))
        pitch = kwargs.get('pitch', self.config.get('pitch', '1.0'))
        
        cache_enabled = kwargs.get('cache', True)
        cache_path = self.get_cache_path(text, voice, rate=rate, pitch=pitch)
        
        if cache_enabled and cache_path.exists():
            return str(cache_path)
        
        try:
            # Build SSML
            ssml = f'''
            <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
                <voice name="{voice}">
                    <prosody rate="{rate}" pitch="{pitch}">{text}</prosody>
                </voice>
            </speak>
            '''
            
            url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
            headers = {
                'Ocp-Apim-Subscription-Key': self.api_key,
                'Content-Type': 'application/ssml+xml',
                'X-Microsoft-OutputFormat': 'audio-24khz-48kbitrate-mono-mp3'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=ssml) as response:
                    if response.status == 200:
                        if cache_enabled:
                            output_path = cache_path
                        else:
                            output_path = Path(tempfile.mktemp(suffix='.mp3'))
                        
                        with open(output_path, 'wb') as f:
                            f.write(await response.read())
                        
                        return str(output_path)
                    else:
                        logger.error(f"Azure TTS failed: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Azure TTS generation failed: {e}")
            return None
    
    def get_available_voices(self) -> List[Dict]:
        if not self.available:
            return []
        
        return [
            {"name": "en-US-AriaNeural", "language": "en-US", "gender": "Female", "description": "Aria - Conversational"},
            {"name": "en-US-JennyNeural", "language": "en-US", "gender": "Female", "description": "Jenny - Customer service"},
            {"name": "en-US-GuyNeural", "language": "en-US", "gender": "Male", "description": "Guy - Professional"},
            {"name": "en-US-DavisNeural", "language": "en-US", "gender": "Male", "description": "Davis - Energetic"},
            {"name": "en-US-AmberNeural", "language": "en-US", "gender": "Female", "description": "Amber - Storytelling"},
            {"name": "en-US-BrandonNeural", "language": "en-US", "gender": "Male", "description": "Brandon - Gaming/tech"},
        ]

class ElevenLabsProvider(TTSProvider):
    """ElevenLabs TTS - AI voices, most natural but paid"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.api_key = config.get('api_key')
        self.available = bool(self.api_key)
        
        if not self.available:
            logger.warning("ElevenLabs TTS: API key not configured")
    
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        if not self.available:
            return None
            
        voice_id = voice or self.config.get('default_voice', 'EXAVITQu4vr4xnSDxMaL')  # Bella
        stability = kwargs.get('stability', self.config.get('stability', 0.5))
        similarity_boost = kwargs.get('similarity_boost', self.config.get('similarity_boost', 0.8))
        
        cache_enabled = kwargs.get('cache', True)
        cache_path = self.get_cache_path(text, voice_id, stability=stability, similarity_boost=similarity_boost)
        
        if cache_enabled and cache_path.exists():
            return str(cache_path)
        
        try:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key
            }
            
            data = {
                "text": text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {
                    "stability": stability,
                    "similarity_boost": similarity_boost
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=headers) as response:
                    if response.status == 200:
                        if cache_enabled:
                            output_path = cache_path
                        else:
                            output_path = Path(tempfile.mktemp(suffix='.mp3'))
                        
                        with open(output_path, 'wb') as f:
                            f.write(await response.read())
                        
                        return str(output_path)
                    else:
                        logger.error(f"ElevenLabs TTS failed: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"ElevenLabs TTS generation failed: {e}")
            return None
    
    def get_available_voices(self) -> List[Dict]:
        if not self.available:
            return []
        
        return [
            {"name": "EXAVITQu4vr4xnSDxMaL", "language": "en", "gender": "Female", "description": "Bella - Young, expressive"},
            {"name": "ErXwobaYiN019PkySvjV", "language": "en", "gender": "Male", "description": "Antoni - Well-rounded"},
            {"name": "VR6AewLTigWG4xSOukaG", "language": "en", "gender": "Male", "description": "Arnold - Crisp, deep"},
            {"name": "pNInz6obpgDQGcFmaJgB", "language": "en", "gender": "Male", "description": "Adam - Deep, professional"},
            {"name": "Xb7hH8MSUJpSbSDYk0k2", "language": "en", "gender": "Female", "description": "Alice - British accent"},
        ]

class PollyTTSProvider(TTSProvider):
    """Amazon Polly TTS - Excellent quality"""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.aws_access_key = config.get('aws_access_key')
        self.aws_secret_key = config.get('aws_secret_key')
        self.region = config.get('region', 'us-east-1')
        self.available = bool(self.aws_access_key and self.aws_secret_key)
        
        if self.available:
            try:
                import boto3
                self.polly = boto3.client(
                    'polly',
                    region_name=self.region,
                    aws_access_key_id=self.aws_access_key,
                    aws_secret_access_key=self.aws_secret_key
                )
            except ImportError:
                logger.warning("boto3 not installed. Install with: pip install boto3")
                self.available = False
        else:
            logger.warning("AWS Polly: Credentials not configured")
    
    async def generate_speech(self, text: str, voice: str = None, **kwargs) -> Optional[str]:
        if not self.available:
            return None
            
        voice_id = voice or self.config.get('default_voice', 'Joanna')
        engine = kwargs.get('engine', self.config.get('engine', 'neural'))  # 'neural' or 'standard'
        
        cache_enabled = kwargs.get('cache', True)
        cache_path = self.get_cache_path(text, voice_id, engine=engine)
        
        if cache_enabled and cache_path.exists():
            return str(cache_path)
        
        try:
            # Use SSML for better control
            ssml_text = f'<speak><prosody rate="medium">{text}</prosody></speak>'
            
            response = self.polly.synthesize_speech(
                Text=ssml_text,
                TextType='ssml',
                OutputFormat='mp3',
                VoiceId=voice_id,
                Engine=engine
            )
            
            if cache_enabled:
                output_path = cache_path
            else:
                output_path = Path(tempfile.mktemp(suffix='.mp3'))
            
            with open(output_path, 'wb') as f:
                f.write(response['AudioStream'].read())
            
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Polly TTS generation failed: {e}")
            return None
    
    def get_available_voices(self) -> List[Dict]:
        if not self.available:
            return []
        
        return [
            {"name": "Joanna", "language": "en-US", "gender": "Female", "description": "Joanna - Neural, natural"},
            {"name": "Matthew", "language": "en-US", "gender": "Male", "description": "Matthew - Neural, professional"},
            {"name": "Ruth", "language": "en-US", "gender": "Female", "description": "Ruth - Neural, young adult"},
            {"name": "Stephen", "language": "en-US", "gender": "Male", "description": "Stephen - Neural, confident"},
            {"name": "Amy", "language": "en-GB", "gender": "Female", "description": "Amy - British accent"},
            {"name": "Brian", "language": "en-GB", "gender": "Male", "description": "Brian - British accent"},
        ]

class TTSManager:
    """Enhanced TTS Manager with multiple providers and intelligent fallback"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.providers = {}
        self.fallback_order = config.get('fallback_order', [
            'edge_tts',
            'azure_tts', 
            'elevenlabs',
            'polly',
            'coqui_tts',
            'gtts'
        ])
        
        # Voice settings per donation tier
        self.tier_settings = config.get('tier_settings', {
            'default': {'provider': 'edge_tts', 'voice': 'en-US-AriaNeural'},
            'vip': {'provider': 'elevenlabs', 'voice': 'EXAVITQu4vr4xnSDxMaL'},
            'premium': {'provider': 'azure_tts', 'voice': 'en-US-JennyNeural'},
        })
        
        # User-specific voice preferences
        self.user_voices = config.get('user_voices', {})
        
        # Initialize providers
        self.init_providers()
        
        # Audio playback
        self.audio_queue = asyncio.Queue()
        self.is_playing = False
        self.init_audio()
        
        # Performance stats
        self.stats = {
            'total_generated': 0,
            'cache_hits': 0,
            'provider_usage': {},
            'generation_times': []
        }
    
    def init_providers(self):
        """Initialize TTS providers"""
        provider_configs = self.config.get('providers', {})
        
        provider_classes = {
            'edge_tts': EdgeTTSProvider,
            'azure_tts': AzureTTSProvider,
            'elevenlabs': ElevenLabsProvider,
            'polly': PollyTTSProvider,
            'gtts': GTTSProvider,
            'coqui_tts': CoquiTTSProvider,
        }
        
        for provider_name, provider_class in provider_classes.items():
            try:
                provider_config = provider_configs.get(provider_name, {})
                provider_config['cache_dir'] = self.config.get('cache_dir', 'tts_cache')
                self.providers[provider_name] = provider_class(provider_config)
                logger.info(f"Initialized TTS provider: {provider_name}")
            except Exception as e:
                logger.error(f"Failed to initialize {provider_name}: {e}")
    
    def init_audio(self):
        """Initialize pygame mixer for audio playback"""
        try:
            pygame.mixer.init()
            logger.info("Audio system initialized")
        except Exception as e:
            logger.error(f"Audio initialization error: {e}")
    
    async def check_provider_health(self):
        """Check health of all providers"""
        health_status = {}
        for name, provider in self.providers.items():
            try:
                health_status[name] = await provider.check_availability()
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                health_status[name] = False
        return health_status
    
    def get_voice_settings(self, username: str, amount: float) -> Dict:
        """Get voice settings based on user and donation amount"""
        # Check user-specific preferences first
        if username in self.user_voices:
            return self.user_voices[username]
        
        # Determine tier based on amount
        tier = 'default'
        if amount >= 100:
            tier = 'vip'
        elif amount >= 25:
            tier = 'premium'
        
        return self.tier_settings.get(tier, self.tier_settings['default'])
    
    async def generate_and_play_tts(self, text: str, username: str, amount: float = 0) -> bool:
        """Generate TTS and play audio with intelligent provider selection"""
        start_time = time.time()
        
        try:
            # Get voice settings
            voice_settings = self.get_voice_settings(username, amount)
            preferred_provider = voice_settings.get('provider', 'edge_tts')
            voice = voice_settings.get('voice')
            
            # Prepare TTS text
            tts_text = f"{username} donated ${amount:.2f} and says: {text}"
            
            # Truncate if too long
            max_length = self.config.get('max_message_length', 500)
            if len(tts_text) > max_length:
                tts_text = tts_text[:max_length] + "... message truncated"
            
            # Try preferred provider first, then fallback
            providers_to_try = [preferred_provider] + [p for p in self.fallback_order if p != preferred_provider]
            
            audio_file = None
            used_provider = None
            
            for provider_name in providers_to_try:
                if provider_name not in self.providers:
                    continue
                
                provider = self.providers[provider_name]
                if not provider.enabled:
                    continue
                
                try:
                    logger.info(f"Trying TTS provider: {provider_name}")
                    audio_file = await provider.generate_speech(
                        text=tts_text,
                        voice=voice,
                        **voice_settings.get('options', {})
                    )
                    
                    if audio_file and os.path.exists(audio_file):
                        used_provider = provider_name
                        break
                        
                except Exception as e:
                    logger.warning(f"TTS provider {provider_name} failed: {e}")
                    continue
            
            if not audio_file:
                logger.error("All TTS providers failed")
                return False
            
            # Play audio
            success = await self.play_audio(audio_file)
            
            # Update stats
            generation_time = time.time() - start_time
            self.stats['total_generated'] += 1
            self.stats['generation_times'].append(generation_time)
            
            if used_provider in self.stats['provider_usage']:
                self.stats['provider_usage'][used_provider] += 1
            else:
                self.stats['provider_usage'][used_provider] = 1
            
            # Keep only last 100 generation times
            if len(self.stats['generation_times']) > 100:
                self.stats['generation_times'].pop(0)
            
            logger.info(f"TTS generated and played successfully using {used_provider} in {generation_time:.2f}s")
            return success
            
        except Exception as e:
            logger.error(f"TTS generation/playback error: {e}")
            return False
    
    async def play_audio(self, audio_file: str) -> bool:
        """Play audio file with async support"""
        try:
            def play_sync():
                pygame.mixer.music.load(audio_file)
                pygame.mixer.music.set_volume(self.config.get('volume', 0.8))
                pygame.mixer.music.play()
                
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
            
            # Run in thread to avoid blocking
            with ThreadPoolExecutor() as executor:
                await asyncio.get_event_loop().run_in_executor(executor, play_sync)
            
            return True
            
        except Exception as e:
            logger.error(f"Audio playback error: {e}")
            return False
    
    def get_available_voices(self) -> Dict[str, List[Dict]]:
        """Get all available voices from all providers"""
        voices = {}
        for name, provider in self.providers.items():
            if provider.enabled:
                try:
                    voices[name] = provider.get_available_voices()
                except Exception as e:
                    logger.error(f"Error getting voices from {name}: {e}")
                    voices[name] = []
        return voices
    
    def get_stats(self) -> Dict:
        """Get TTS performance statistics"""
        avg_generation_time = 0
        if self.stats['generation_times']:
            avg_generation_time = sum(self.stats['generation_times']) / len(self.stats['generation_times'])
        
        return {
            **self.stats,
            'average_generation_time': avg_generation_time,
            'provider_health': asyncio.run(self.check_provider_health())
        }
    
    def set_user_voice(self, username: str, provider: str, voice: str):
        """Set custom voice for a specific user"""
        self.user_voices[username] = {
            'provider': provider,
            'voice': voice
        }
        
        # Save to config
        if 'user_voices' not in self.config:
            self.config['user_voices'] = {}
        self.config['user_voices'][username] = self.user_voices[username]
    
    def clear_cache(self, provider: str = None):
        """Clear TTS cache for specific provider or all providers"""
        if provider and provider in self.providers:
            cache_dir = self.providers[provider].cache_dir
            for file in cache_dir.glob('*.mp3'):
                file.unlink()
            logger.info(f"Cleared cache for {provider}")
        else:
            for provider_obj in self.providers.values():
                for file in provider_obj.cache_dir.glob('*.mp3'):
                    file.unlink()
            logger.info("Cleared all TTS caches")

# Example configuration
EXAMPLE_CONFIG = {
    "volume": 0.8,
    "max_message_length": 500,
    "cache_dir": "tts_cache",
    "fallback_order": ["edge_tts", "azure_tts", "elevenlabs", "polly", "coqui_tts", "gtts"],
    "tier_settings": {
        "default": {
            "provider": "edge_tts",
            "voice": "en-US-AriaNeural",
            "options": {"rate": "+10%", "pitch": "+0Hz"}
        },
        "premium": {
            "provider": "azure_tts", 
            "voice": "en-US-JennyNeural",
            "options": {"rate": "1.1", "pitch": "1.0"}
        },
        "vip": {
            "provider": "elevenlabs",
            "voice": "EXAVITQu4vr4xnSDxMaL",
            "options": {"stability": 0.6, "similarity_boost": 0.9}
        }
    },
    "user_voices": {
        "StreamerName": {
            "provider": "elevenlabs",
            "voice": "pNInz6obpgDQGcFmaJgB"  # Adam voice
        }
    },
    "providers": {
        "edge_tts": {
            "enabled": True,
            "default_voice": "en-US-AriaNeural",
            "use_ssml": False,  # Simple text mode
            "rate": "+0%",      # Only used if use_ssml is true  
            "pitch": "+0Hz"     # Only used if use_ssml is true
        },
        "azure_tts": {
            "enabled": False,  # Set to True and add API key
            "api_key": "YOUR_AZURE_SPEECH_KEY",
            "region": "eastus",
            "default_voice": "en-US-AriaNeural"
        },
        "elevenlabs": {
            "enabled": False,  # Set to True and add API key
            "api_key": "YOUR_ELEVENLABS_API_KEY",
            "default_voice": "EXAVITQu4vr4xnSDxMaL",  # Bella
            "stability": 0.5,
            "similarity_boost": 0.8
        },
        "polly": {
            "enabled": False,  # Set to True and add AWS credentials
            "aws_access_key": "YOUR_AWS_ACCESS_KEY",
            "aws_secret_key": "YOUR_AWS_SECRET_KEY",
            "region": "us-east-1",
            "default_voice": "Joanna",
            "engine": "neural"
        },
        "gtts": {
            "enabled": True,
            "language": "en",
            "slow": False
        },
        "coqui_tts": {
            "enabled": False,  # Requires significant setup
            "model": "tts_models/en/ljspeech/tacotron2-DDC"
        }
    }
}

def create_tts_config():
    """Create a sample configuration file for TTS"""
    config_file = "tts_config.json"
    
    if not os.path.exists(config_file):
        with open(config_file, 'w') as f:
            json.dump(EXAMPLE_CONFIG, f, indent=2)
        print(f"Created example config: {config_file}")
        print("Edit this file to configure your TTS providers and API keys.")
    
    return config_file

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

# Integration with existing TTS Queue System
class IntegratedTTSManager:
    """Integration wrapper for the main TTS Queue System"""
    
    def __init__(self, original_config: Dict):
        # Load enhanced TTS config
        enhanced_config_file = create_tts_config()
        
        with open(enhanced_config_file, 'r') as f:
            enhanced_config = json.load(f)
        
        # Initialize enhanced TTS manager
        self.enhanced_tts = TTSManager(enhanced_config)
        
        # Keep reference to original config for compatibility
        self.original_config = original_config
        
        logger.info("TTS Manager integrated successfully")
    
    def generate_and_play_tts(self, text: str, username: str, amount: float = 0) -> bool:
        """Async wrapper for the TTS system"""
        try:
            # Run the async function
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self.enhanced_tts.generate_and_play_tts(text, username, amount)
            )
            loop.close()
            return result
        except Exception as e:
            logger.error(f"TTS error: {e}")
            return False
    
    def get_voice_options(self) -> Dict:
        """Get available voice options for dashboard"""
        return self.enhanced_tts.get_available_voices()
    
    def get_tts_stats(self) -> Dict:
        """Get TTS performance statistics"""
        return self.enhanced_tts.get_stats()
    
    def set_user_voice_preference(self, username: str, provider: str, voice: str):
        """Set custom voice for a user"""
        self.enhanced_tts.set_user_voice(username, provider, voice)
    
    def clear_tts_cache(self, provider: str = None):
        """Clear TTS cache"""
        self.enhanced_tts.clear_cache(provider)

class TTSQueueSystem:
    """Main TTS Queue System orchestrator"""
    
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager(self.config.get('database.path', 'tts_queue.db'))
        self.tts = IntegratedTTSManager(self.config)
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
                if self.tts.generate_and_play_tts(message, username, amount):
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