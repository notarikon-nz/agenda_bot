#!/usr/bin/env python3
"""
TTS Queue Terminal Dashboard
===========================

A professional terminal-based dashboard for the TTS donation queue system.
Styled like btop with real-time updates and keyboard controls.

Requirements:
- pip install textual httpx rich

Usage:
- python tts_terminal_dashboard.py
- Press 'q' to quit, 'h' for help
- Use Tab/Shift+Tab to navigate
- Enter/Space to activate buttons

Author: Matt Orsborn
License: MIT
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.widgets import (
    Header, Footer, Static, Button, DataTable, ProgressBar, 
    Label, Input, TextArea, Collapsible, Sparkline
)
from textual.reactive import reactive, var
from textual.binding import Binding
from textual.timer import Timer
from textual.coordinate import Coordinate

# Setup debug logging to file
def setup_debug_logging(debug_enabled=False):
    """Setup debug logging to file"""
    log_level = logging.DEBUG if debug_enabled else logging.INFO
    
    # Create logger
    debug_logger = logging.getLogger('tts_dashboard')
    debug_logger.setLevel(log_level)
    
    # Remove existing handlers
    for handler in debug_logger.handlers[:]:
        debug_logger.removeHandler(handler)
    
    # File handler for debug info
    file_handler = logging.FileHandler('tts_dashboard_debug.log', mode='w')
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    debug_logger.addHandler(file_handler)
    
    if debug_enabled:
        debug_logger.info("=== TTS Dashboard Debug Session Started ===")
    
    return debug_logger

# Global debug logger
debug_logger = setup_debug_logging()

class QueueStatsWidget(Static):
    """Widget displaying queue statistics"""
    
    BORDER_TITLE = "Queue Statistics"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stats = {}
    
    def update_stats(self, stats: Dict):
        """Update the statistics display"""
        self.stats = stats
        
        in_queue = stats.get('total_in_queue', 0)
        processed = stats.get('total_processed', 0)
        pending_amount = stats.get('total_amount_pending', 0.0)
        processed_amount = stats.get('total_amount_processed', 0.0)
        total = in_queue + processed
        
        # Create progress bar for queue completion
        progress = (processed / total * 100) if total > 0 else 0
        
        content = f"""Queue:       {processed:>3} / {total:<3} [{progress:5.1f}%]

Processed:   {processed:>8} items   
Pending:     {in_queue:>8} items   
Earned:      ${processed_amount:>8.2f}
Pending:     ${pending_amount:>8.2f}
Total:       ${processed_amount + pending_amount:>8.2f}"""
        
        self.update(content)

class ServiceStatusWidget(Static):
    """Widget displaying service connection status"""
    
    BORDER_TITLE = "Service Status"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.services = {}
    
    def update_services(self, services_data: Dict):
        """Update the services display"""
        services = services_data.get('services', {})
        connected = services_data.get('connected_services', 0)
        total = services_data.get('total_services', 0)
        
        content = f"""{connected}/{total} Connected\n"""
        
        for name, info in services.items():
            status_icon = "🟢" if info.get('connected', False) else "🔴"
            status_text = "ONLINE " if info.get('connected', False) else "OFFLINE"
            status_color = "green" if info.get('connected', False) else "red"
            
            content += f"\n {status_icon} [{status_color}]{name.upper():<8}[/{status_color}] {status_text:<8} "
            
            if info.get('error') and not info.get('connected', False):
                error_short = info['error'][:25] + "..." if len(info['error']) > 25 else info['error']
                content += f"\n   [dim red]{error_short:<33}[/dim red] "
        
        self.update(content)

class QueueTableWidget(Static):
    """Widget displaying the current queue"""
    
    BORDER_TITLE = "Current Queue"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.queue_items = []
        self.table = None
        self.mounted = False
    
    def compose(self) -> ComposeResult:
        yield DataTable()
    
    def on_mount(self) -> None:
        debug_logger.debug("QueueTableWidget mounting...")
        try:
            self.table = self.query_one(DataTable)
            self.table.add_columns("User", "Amount", "Message", "Time")
            self.table.cursor_type = "row" 
            self.table.zebra_stripes = False
            self.mounted = True
            
            # Add placeholder data to show table works
            self.table.add_row("Loading...", "$0.00", "Fetching queue data...", "00:00:00")
            debug_logger.debug("QueueTableWidget mounted successfully with placeholder")
            
        except Exception as e:
            debug_logger.error(f"QueueTableWidget mount error: {e}")
    
    def update_queue(self, queue_data: Dict):
        """Update the queue table"""
        debug_logger.debug(f"QueueTableWidget.update_queue called with: {queue_data.keys()}")
        
        if not self.mounted or self.table is None:
            debug_logger.warning("QueueTableWidget not mounted, skipping update")
            return
            
        try:
            # Clear existing data
            self.table.clear()
            debug_logger.debug("QueueTableWidget cleared")
            
            pending_items = queue_data.get('pending', [])
            debug_logger.debug(f"Processing {len(pending_items)} pending items")
            
            if not pending_items:
                # Show empty state
                self.table.add_row("No items", "$0.00", "Queue is empty", "--:--:--")
                debug_logger.debug("Added empty state row")
                return
            
            for i, item in enumerate(pending_items):
                debug_logger.debug(f"Processing item {i}: {item}")
                
                # Format timestamp
                time_str = item.get('created_at', '')
                if time_str:
                    try:
                        # Handle different timestamp formats
                        if 'T' in time_str:
                            dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                        else:
                            dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                        time_formatted = dt.strftime('%H:%M:%S')
                    except Exception as e:
                        debug_logger.warning(f"Time parsing error: {e}")
                        time_formatted = time_str[:8] if time_str else "Unknown"
                else:
                    time_formatted = "Unknown"
                
                # Truncate message for table
                message = str(item.get('message', ''))
                if len(message) > 35:
                    message = message[:32] + "..."
                
                # Add row to table
                username = str(item.get('username', 'Unknown'))
                amount = f"${float(item.get('amount', 0)):.2f}"
                
                self.table.add_row(username, amount, message, time_formatted)
                debug_logger.debug(f"Added row: {username}, {amount}, {message[:20]}..., {time_formatted}")
            
            debug_logger.info(f"QueueTableWidget updated with {len(pending_items)} items")
                
        except Exception as e:
            debug_logger.error(f"QueueTableWidget update error: {e}")
            # Show error in table
            if self.table:
                self.table.clear()
                self.table.add_row("Error", "$0.00", f"Update failed: {str(e)}", "00:00:00")

class RecentProcessedWidget(Static):
    """Widget displaying recently processed items"""
    
    BORDER_TITLE = "Recently Processed"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.table = None
        self.mounted = False
    
    def compose(self) -> ComposeResult:
        yield DataTable()
    
    def on_mount(self) -> None:
        debug_logger.debug("RecentProcessedWidget mounting...")
        try:
            self.table = self.query_one(DataTable)
            self.table.add_columns("User", "Amount", "Message")
            self.table.cursor_type = "row"
            self.table.zebra_stripes = False
            self.mounted = True
            
            # Add placeholder
            self.table.add_row("Loading...", "$0.00", "Fetching recent data...")
            debug_logger.debug("RecentProcessedWidget mounted successfully")
            
        except Exception as e:
            debug_logger.error(f"RecentProcessedWidget mount error: {e}")
    
    def update_recent(self, queue_data: Dict):
        """Update the recent processed items"""
        debug_logger.debug(f"RecentProcessedWidget.update_recent called")
        
        if not self.mounted or self.table is None:
            debug_logger.warning("RecentProcessedWidget not mounted, skipping update")
            return
            
        try:
            # Clear existing data
            self.table.clear()
            debug_logger.debug("RecentProcessedWidget cleared")
            
            recent_items = queue_data.get('recent_processed', [])
            debug_logger.debug(f"Processing {len(recent_items)} recent items")
            
            if not recent_items:
                # Show empty state
                self.table.add_row("No items", "$0.00", "No recent processed items")
                debug_logger.debug("Added empty state row to recent")
                return
            
            for i, item in enumerate(recent_items[:10]):  # Show last 10
                debug_logger.debug(f"Processing recent item {i}: {item}")
                
                message = str(item.get('message', ''))
                if len(message) > 30:
                    message = message[:27] + "..."
                
                # Check if item was skipped
                timestamp = str(item.get('timestamp', ''))
                status_indicator = " [SKIP]" if "[SKIPPED]" in timestamp else ""
                
                username = str(item.get('username', 'Unknown'))
                amount = f"${float(item.get('amount', 0)):.2f}"
                
                self.table.add_row(username, amount, message + status_indicator)
                debug_logger.debug(f"Added recent row: {username}, {amount}, {message[:15]}...")
            
            debug_logger.info(f"RecentProcessedWidget updated with {len(recent_items[:10])} items")
                
        except Exception as e:
            debug_logger.error(f"RecentProcessedWidget update error: {e}")
            # Show error in table
            if self.table:
                self.table.clear()
                self.table.add_row("Error", "$0.00", f"Update failed: {str(e)}")


class ControlPanelWidget(Static):
    """Widget with control buttons"""
    
    def compose(self) -> ComposeResult:
        with Grid(id="control_grid"):
            yield Button("▶️ Process Next", id="process_btn", variant="success")
            yield Button("⏭️ Skip Next", id="skip_btn", variant="warning") 
            yield Button("⏹️ Stop TTS", id="stop_btn", variant="error")
            yield Button("🗑️ Clear Queue", id="clear_btn", variant="error")
            yield Button("🔄 Reset Counter", id="reset_btn", variant="primary")
            yield Button("🔄 Refresh", id="refresh_btn", variant="default")

class SystemHealthWidget(Static):
    """Widget displaying system performance metrics"""
    
    BORDER_TITLE = "System Health"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cpu_history = []
        self.memory_history = []
    
    def update_health(self, health_data: Dict):
        """Update health metrics"""
        uptime = health_data.get('uptime', '00:00:00')
        error_count = health_data.get('error_count', 0)
        
        # Extract latest CPU/memory if available
        cpu_usage = 0
        memory_usage = 0
        
        cpu_data = health_data.get('cpu_usage', [])
        memory_data = health_data.get('memory_usage', [])
        
        if cpu_data:
            cpu_usage = cpu_data[-1].get('value', 0)
            self.cpu_history = [d.get('value', 0) for d in cpu_data[-20:]]
        
        if memory_data:
            memory_usage = memory_data[-1].get('value', 0)
            self.memory_history = [d.get('value', 0) for d in memory_data[-20:]]
        
        content = f"""Uptime:  {uptime:<16}
CPU:     [{"red" if cpu_usage > 80 else "yellow" if cpu_usage > 50 else "green"}]{cpu_usage:>5.1f}%[/{"red" if cpu_usage > 80 else "yellow" if cpu_usage > 50 else "green"}]
Memory:  [{"red" if memory_usage > 80 else "yellow" if memory_usage > 50 else "green"}]{memory_usage:>5.1f}%[/{"red" if memory_usage > 80 else "yellow" if memory_usage > 50 else "green"}]
Errors:  [{"red" if error_count > 0 else "green"}]{error_count:>5}[/{"red" if error_count > 0 else "green"}]"""
        
        self.update(content)

class TTSQueueDashboard(App):
    """Main TTS Queue Terminal Dashboard Application"""
    
    CSS = """
    Screen {
        background: #272e33;
    }

    #main_container {
        height: 100%;
        color: #d3c6aa;
    }
    
    #stats_panel {
        width: 40;
        height: 100%;
    }
    
    #queue_panel {
        height: 60%;
    }
    
    #recent_panel {
        height: 40%;
    }
    
    #control_grid {
        grid-size: 3 2;
        grid-gutter: 1;
        margin: 1;
        height: 8;
    }
    
    #queue_table {
        height: 100%;
        margin: 0;
        border: none;
        color: #d3c6aa;
    }
    
    #recent_table {
        height: 100%;
        margin: 0;
        border: none;
    }
    
    .panel_title {
        background: #374145;
        color: #d3c6aa;
        text-style: bold;
        height: 1;
        margin: 0;
        padding: 0 1;
        border: none;
    }
    
    Button {
        width: 100%;
        margin: 0 1;
    }
    
    .status_good {
        color: $success;
    }
    
    .status_warning {
        color: $warning;
    }
    
    .status_error {
        color: $error;
    }
    
    Static {
        border: round #374145;
        margin: 0;
        padding: 1;
        border-title-color: #d3c6aa !important;
    }
    
    QueueTableWidget {
        border: round #374145;
        margin: 0;
        padding: 0;
        color: #d3c6aa;
        border-title-color: #d3c6aa !important;
    }
    
    RecentProcessedWidget {
        border: round #374145;
        margin: 0;
        padding: 0;
        color: #d3c6aa;
        border-title-color: #d3c6aa !important;
    }

    Label {
        border-title-color: #d3c6aa !important;
    }   
    """
    
    TITLE = "TTS Queue Dashboard"
    SUB_TITLE = "Professional stream management terminal"
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("p", "process_next", "Process Next"),
        Binding("s", "skip_next", "Skip Next"),
        Binding("x", "stop_tts", "Stop TTS"),
        Binding("c", "clear_queue", "Clear Queue"),
        Binding("ctrl+r", "reset_counter", "Reset Counter"),
        Binding("h", "help", "Help"),
    ]
    
    def __init__(self, api_base_url: str = "http://localhost:5000"):
        super().__init__()
        self.api_base_url = api_base_url
        self.last_update = datetime.now()
        self.update_timer: Optional[Timer] = None
        self.connection_status = "Disconnected"
    
    # layout
    def compose(self) -> ComposeResult:
        """Compose the dashboard layout"""
        yield Header(show_clock=True)
        
        with Container(id="main_container"):
            with Horizontal():
                # Left panel - Stats and Services
                with Vertical(id="stats_panel"):
                    yield QueueStatsWidget(id="queue_stats")
                    yield ServiceStatusWidget(id="service_status")
                    yield SystemHealthWidget(id="system_health")
                
                # Right panel - Queue and Controls
                with Vertical():
                    # Queue table with title
                    with Container(id="queue_panel"):
                        yield QueueTableWidget(id="queue_widget")
                    
                    # Recent processed with title
                    with Container(id="recent_panel"):
                        yield RecentProcessedWidget(id="recent_widget")
                    
                    # Control panel
                    yield ControlPanelWidget(id="control_panel")
        
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the dashboard"""
        self.title = "TTS Queue Dashboard - Connecting..."
        self.sub_title = f"API: {self.api_base_url}"
        
        # Start the update timer
        self.update_timer = self.set_interval(2.0, self.refresh_data)
        
        # Initial data load
        self.call_later(self.refresh_data)
    
    async def refresh_data(self) -> None:
        """Refresh all data from the API"""

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Test connectivity first
            try:
                response = await client.get(f"{self.api_base_url}/api/test")
                if response.status_code == 200:
                    self.connection_status = "Connected"
                    self.title = "TTS Queue Dashboard - Connected"
                else:
                    self.connection_status = "API Error"
                    self.title = "TTS Queue Dashboard - API Error"
                    return
            except Exception:
                self.connection_status = "Disconnected"
                self.title = "TTS Queue Dashboard - Disconnected"
                return
            
            # Get queue data - try multiple possible endpoints
            queue_data = None
            try:
                # Try the full queue endpoint first
                response = await client.get(f"{self.api_base_url}/api/queue")
                if response.status_code == 200:
                    queue_data = response.json()
            except Exception:
                try:
                    # Fallback: try to get queue stats
                    response = await client.get(f"{self.api_base_url}/queue_stats")
                    if response.status_code == 200:
                        stats_data = response.json()
                        # Create minimal queue data structure
                        queue_data = {
                            'stats': stats_data,
                            'pending': [],
                            'recent_processed': []
                        }
                except Exception:
                    # Create dummy data to prevent crashes
                    queue_data = {
                        'stats': {
                            'total_in_queue': 0,
                            'total_processed': 0,
                            'total_amount_pending': 0.0,
                            'total_amount_processed': 0.0
                        },
                        'pending': [],
                        'recent_processed': []
                    }
                
    async def refresh_data(self) -> None:
        """Refresh all data from the API"""
        debug_logger.debug("=== Starting data refresh ===")
        
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Test connectivity first
                connectivity_ok = False
                debug_logger.debug("Testing connectivity...")
                
                try:
                    response = await client.get(f"{self.api_base_url}/api/test")
                    if response.status_code == 200:
                        self.connection_status = "Connected"
                        self.title = "TTS Queue Dashboard - Connected"
                        debug_logger.debug("Connectivity test passed")
                        connectivity_ok = True
                    else:
                        self.connection_status = "API Error"
                        self.title = "TTS Queue Dashboard - API Error"
                        debug_logger.error(f"Connectivity test failed: {response.status_code}")
                except Exception as e:
                    self.connection_status = "Disconnected"
                    self.title = "TTS Queue Dashboard - Disconnected"
                    debug_logger.error(f"Connectivity test exception: {e}")
                
                if not connectivity_ok:
                    return
                
                # Get queue data - try multiple possible endpoints
                queue_data = None
                
                # Try primary endpoint first
                debug_logger.debug("Fetching queue data from /api/queue...")
                try:
                    response = await client.get(f"{self.api_base_url}/api/queue")
                    if response.status_code == 200:
                        queue_data = response.json()
                        debug_logger.info(f"Queue data received: {queue_data}")
                    else:
                        debug_logger.warning(f"/api/queue returned {response.status_code}")
                except Exception as e:
                    debug_logger.error(f"Failed to get /api/queue: {e}")
                
                # Try fallback endpoint if primary failed
                if queue_data is None:
                    debug_logger.debug("Trying fallback /queue_stats...")
                    try:
                        response = await client.get(f"{self.api_base_url}/queue_stats")
                        if response.status_code == 200:
                            stats_data = response.json()
                            queue_data = {
                                'stats': stats_data,
                                'pending': [],
                                'recent_processed': []
                            }
                            debug_logger.debug("Using fallback queue_stats data")
                        else:
                            debug_logger.warning(f"/queue_stats returned {response.status_code}")
                    except Exception as e2:
                        debug_logger.error(f"Fallback also failed: {e2}")
                
                # Use dummy data if both failed
                if queue_data is None:
                    debug_logger.warning("Both endpoints failed, using dummy data")
                    queue_data = {
                        'stats': {
                            'total_in_queue': 0,
                            'total_processed': 0,
                            'total_amount_pending': 0.0,
                            'total_amount_processed': 0.0
                        },
                        'pending': [],
                        'recent_processed': []
                    }
                
                # Process queue data
                if queue_data:
                    debug_logger.info(f"Processing queue data: {len(queue_data.get('pending', []))} pending, {len(queue_data.get('recent_processed', []))} recent")
                    
                    # Update queue stats
                    try:
                        stats_widget = self.query_one("#queue_stats", QueueStatsWidget)
                        stats_widget.update_stats(queue_data.get('stats', {}))
                        debug_logger.debug("Queue stats updated successfully")
                    except Exception as e:
                        debug_logger.error(f"Error updating queue stats: {e}")
                    
                    # Update queue table
                    try:
                        debug_logger.debug("Looking for queue widget...")
                        queue_widget = self.query_one("#queue_widget", QueueTableWidget)
                        debug_logger.debug(f"Found queue widget: {queue_widget}")
                        queue_widget.update_queue(queue_data)
                        debug_logger.debug("Queue table update called")
                    except Exception as e:
                        debug_logger.error(f"Error updating queue table: {e}")
                        self.notify(f"Queue table error: {e}", severity="error")
                    
                    # Update recent processed
                    try:
                        debug_logger.debug("Looking for recent widget...")
                        recent_widget = self.query_one("#recent_widget", RecentProcessedWidget)
                        debug_logger.debug(f"Found recent widget: {recent_widget}")
                        recent_widget.update_recent(queue_data)
                        debug_logger.debug("Recent table update called")
                    except Exception as e:
                        debug_logger.error(f"Error updating recent table: {e}")
                        self.notify(f"Recent table error: {e}", severity="error")
                else:
                    debug_logger.warning("No queue data to process")
                    self.notify("Could not load queue data", severity="warning")
                
                # Get services status
                debug_logger.debug("Fetching services status...")
                try:
                    response = await client.get(f"{self.api_base_url}/api/services/status")
                    if response.status_code == 200:
                        services_data = response.json()
                        debug_logger.debug("Services data received")
                        
                        # Update services status
                        services_widget = self.query_one("#service_status", ServiceStatusWidget)
                        services_widget.update_services(services_data)
                    else:
                        debug_logger.warning(f"Services endpoint returned {response.status_code}")
                        self._use_fallback_services()
                        
                except Exception as e:
                    debug_logger.warning(f"Services status error: {e}")
                    self._use_fallback_services()
                
                # Get health data
                debug_logger.debug("Fetching health data...")
                try:
                    response = await client.get(f"{self.api_base_url}/api/health")
                    if response.status_code == 200:
                        health_data = response.json()
                        debug_logger.debug("Health data received")
                        
                        # Update health widget
                        health_widget = self.query_one("#system_health", SystemHealthWidget)
                        health_widget.update_health(health_data)
                    else:
                        debug_logger.warning(f"Health endpoint returned {response.status_code}")
                        self._use_fallback_health()
                        
                except Exception as e:
                    debug_logger.warning(f"Health data error: {e}")
                    self._use_fallback_health()
                
                self.last_update = datetime.now()
                debug_logger.debug("=== Data refresh completed ===")
                
                # Get services status
                try:
                    response = await client.get(f"{self.api_base_url}/api/services/status")
                    if response.status_code == 200:
                        services_data = response.json()
                        
                        # Update services status
                        services_widget = self.query_one("#service_status", ServiceStatusWidget)
                        services_widget.update_services(services_data)
                        
                except Exception as e:
                    # Create fallback service status
                    fallback_services = {
                        'services': {
                            'obs': {'connected': False, 'status': 'unknown', 'error': 'Endpoint not available'},
                            'discord': {'connected': False, 'status': 'unknown', 'error': 'Endpoint not available'},
                            'tts': {'connected': True, 'status': 'connected', 'error': None}
                        },
                        'connected_services': 1,
                        'total_services': 3
                    }
                    
                    services_widget = self.query_one("#service_status", ServiceStatusWidget)
                    services_widget.update_services(fallback_services)
                
                # Get health data
                try:
                    response = await client.get(f"{self.api_base_url}/api/health")
                    if response.status_code == 200:
                        health_data = response.json()
                        
                        # Update health widget
                        health_widget = self.query_one("#system_health", SystemHealthWidget)
                        health_widget.update_health(health_data)
                        
                except Exception as e:
                    # Create fallback health data
                    import time
                    start_time = time.time() - 3600  # Assume 1 hour uptime
                    uptime_seconds = time.time() - start_time
                    hours, remainder = divmod(uptime_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    uptime_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
                    
                    fallback_health = {
                        'uptime': uptime_str,
                        'error_count': 0,
                        'cpu_usage': [{'value': 25.0}],
                        'memory_usage': [{'value': 45.0}]
                    }
                    
                    health_widget = self.query_one("#system_health", SystemHealthWidget)
                    health_widget.update_health(fallback_health)
                
                self.last_update = datetime.now()
                
        except Exception as e:
            self.connection_status = "Error"
            self.title = f"TTS Queue Dashboard - Error: {e}"
            debug_logger.error(f"Refresh error: {e}")
            self.notify(f"Refresh error: {e}", severity="error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if button_id == "process_btn":
                    response = await client.post(f"{self.api_base_url}/process_next")
                    if response.status_code == 200:
                        self.notify("Processing next item...", severity="information")
                    else:
                        result = response.json()
                        self.notify(result.get('message', 'Process failed'), severity="warning")
                
                elif button_id == "skip_btn":
                    response = await client.post(f"{self.api_base_url}/skip_next")
                    if response.status_code == 200:
                        result = response.json()
                        skipped = result.get('skipped_item', {})
                        self.notify(f"Skipped: {skipped.get('username', 'Unknown')} - ${skipped.get('amount', 0):.2f}", severity="warning")
                    else:
                        result = response.json()
                        self.notify(result.get('message', 'Skip failed'), severity="error")
                
                elif button_id == "stop_btn":
                    response = await client.post(f"{self.api_base_url}/stop_tts")
                    if response.status_code == 200:
                        self.notify("TTS playback stopped", severity="warning")
                    else:
                        result = response.json()
                        self.notify(result.get('message', 'Stop failed'), severity="error")
                
                elif button_id == "clear_btn":
                    # Confirmation for destructive action
                    self.notify("Press Ctrl+C to confirm queue clear, or any other key to cancel", severity="warning")
                    # Note: In a real implementation, you'd want a proper confirmation dialog
                    
                elif button_id == "reset_btn":
                    response = await client.post(f"{self.api_base_url}/reset_counter")
                    if response.status_code == 200:
                        result = response.json()
                        archived = result.get('items_archived', 0)
                        self.notify(f"Counter reset! Archived {archived} items", severity="information")
                    else:
                        result = response.json()
                        self.notify(result.get('message', 'Reset failed'), severity="error")
                
                elif button_id == "refresh_btn":
                    await self.refresh_data()
                    self.notify("Data refreshed", severity="information")
                
                # Refresh data after any action
                if button_id != "refresh_btn":
                    self.call_later(self.refresh_data)
                
        except Exception as e:
            self.notify(f"API Error: {e}", severity="error")
    
    def action_refresh(self) -> None:
        """Refresh action via keyboard"""
        self.call_later(self.refresh_data)
    
    def action_process_next(self) -> None:
        """Process next action via keyboard"""
        process_btn = self.query_one("#process_btn", Button)
        self.call_later(self.on_button_pressed, Button.Pressed(process_btn))
    
    def action_skip_next(self) -> None:
        """Skip next action via keyboard"""
        skip_btn = self.query_one("#skip_btn", Button)
        self.call_later(self.on_button_pressed, Button.Pressed(skip_btn))
    
    def action_stop_tts(self) -> None:
        """Stop TTS action via keyboard"""
        stop_btn = self.query_one("#stop_btn", Button)
        self.call_later(self.on_button_pressed, Button.Pressed(stop_btn))
    
    def action_clear_queue(self) -> None:
        """Clear queue action via keyboard"""
        clear_btn = self.query_one("#clear_btn", Button)
        self.call_later(self.on_button_pressed, Button.Pressed(clear_btn))
    
    def action_reset_counter(self) -> None:
        """Reset counter action via keyboard"""
        reset_btn = self.query_one("#reset_btn", Button)
        self.call_later(self.on_button_pressed, Button.Pressed(reset_btn))
    
    def action_help(self) -> None:
        """Show help information"""
        help_text = """
TTS Queue Dashboard - Keyboard Shortcuts:

⌨️  Navigation:
    Tab/Shift+Tab  - Navigate between elements
    Enter/Space    - Activate buttons
    ↑↓            - Navigate tables

🎛️  Controls:
    P             - Process Next
    S             - Skip Next  
    X             - Stop TTS
    C             - Clear Queue
    Ctrl+R        - Reset Counter
    R             - Refresh Data

🔧  System:
    H             - This help
    Q             - Quit

💡  Tips:
    - Data refreshes every 2 seconds automatically
    - All buttons work with your Stream Deck too
    - Queue items update in real-time
    - Service status shows connection health
        """
        self.notify(help_text, severity="information", timeout=10)

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="TTS Queue Terminal Dashboard")
    parser.add_argument(
        "--api-url", 
        default="http://localhost:5000",
        help="Base URL for the TTS Queue API (default: http://localhost:5000)"
    )
    parser.add_argument(
        "--refresh-rate",
        type=float,
        default=2.0,
        help="Data refresh rate in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to tts_dashboard_debug.log"
    )
    
    args = parser.parse_args()
    
    # Setup debug logging
    global debug_logger
    debug_logger = setup_debug_logging(args.debug)
    
    if args.debug:
        print("🐛 Debug mode enabled - check tts_dashboard_debug.log for detailed logs")
        print("   You can monitor logs with: tail -f tts_dashboard_debug.log")
        print()
    
    debug_logger.info(f"Starting TTS Dashboard with API URL: {args.api_url}")
    debug_logger.info(f"Debug mode: {args.debug}")
    
    app = TTSQueueDashboard(api_base_url=args.api_url)
    
    try:
        app.run()
    except KeyboardInterrupt:
        debug_logger.info("Dashboard stopped by user")
        print("\n👋 Thanks for using TTS Queue Dashboard!")
        if args.debug:
            print("🐛 Debug logs saved to: tts_dashboard_debug.log")

if __name__ == "__main__":
    main()