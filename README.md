# TTS Donation Queue Setup Instructions

## Prerequisites

### 1. Install Required Python Packages
```bash
pip install obswebsocket-python discord-webhook gtts pygame flask requests
```

### 2. OBS Setup
1. Install OBS Studio
2. Enable WebSocket plugin:
   - Go to Tools → obs-websocket Settings
   - Enable "Enable WebSocket server"
   - Set port to 4455 (default)
   - Set password (optional but recommended)

3. Create a Text Source:
   - Add a "Text (GDI+)" source to your scene
   - Name it "TTS Queue Counter"
   - Style it as desired (font, color, etc.)

### 3. Discord Setup (Optional)
1. Create a Discord webhook:
   - Go to your Discord server
   - Server Settings → Integrations → Webhooks
   - Create New Webhook
   - Copy the webhook URL

### 4. Stream Deck Setup
1. Install Stream Deck software
2. Create a new button with "System → Website" action
3. Set URL to: `http://localhost:5000/process_next`
4. Set method to POST
5. Customize button appearance as desired

## Configuration

### 1. Create config.json
The system will create a default config.json file on first run. Edit it to match your setup:

```json
{
  "obs": {
    "host": "localhost",
    "port": 4455,
    "password": "your_obs_password",
    "queue_scene": "Stream",
    "queue_text_source": "TTS Queue Counter"
  },
  "discord": {
    "webhook_url": "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL",
    "enabled": true
  },
  "tts": {
    "language": "en",
    "slow": false,
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
```

### 2. Test Audio
Make sure your system audio is working and pygame can play sounds. The system uses the default audio output.

## Running the System

### 1. Start the System
```bash
python3 tts_queue_system.py
```

### 2. Add Donations (For Testing)
You can add donations via HTTP POST to test the system:

```bash
curl -X POST http://localhost:5000/add_donation \
  -H "Content-Type: application/json" \
  -d '{
    "username": "TestUser",
    "message": "Hello from the donation queue!",
    "amount": 5.00
  }'
```

### 3. Process Queue Items
- Press your Stream Deck button to process the next item
- Or make a POST request to `/process_next`

### 4. Monitor Queue
Check queue status:
```bash
curl http://localhost:5000/queue_stats
```

## Integration with Donation Platforms

### For Streamlabs/StreamElements Integration
Create a custom overlay or browser source that sends donations to your queue:

```javascript
// Example JavaScript for donation alerts
function handleDonation(donationData) {
    fetch('http://localhost:5000/add_donation', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            username: donationData.name,
            message: donationData.message,
            amount: donationData.amount
        })
    });
}
```

### For Twitch Integration
You can integrate with Twitch EventSub or use a bot to capture donations and send them to the queue.

## Stream Deck Advanced Configuration

### Multiple Buttons
You can create multiple Stream Deck buttons for different functions:

1. **Process Next** - `POST /process_next`
2. **Queue Stats** - `GET /queue_stats` (display in Stream Deck)
3. **Clear Queue** - You can extend the system to add this endpoint

### Stream Deck Button Setup Details
1. **Icon**: Use a microphone or TTS-related icon
2. **Title**: "Process TTS"
3. **URL**: `http://localhost:5000/process_next`
4. **Method**: POST
5. **Headers**: `Content-Type: application/json`

## Troubleshooting

### Common Issues

1. **Audio not playing**:
   - Check system audio levels
   - Verify pygame mixer initialization
   - Test with a simple audio file

2. **OBS not updating**:
   - Verify OBS WebSocket is enabled
   - Check the text source name matches config
   - Ensure OBS is running

3. **Stream Deck not working**:
   - Verify the server is running on correct port
   - Check firewall settings
   - Test the endpoint with curl first

4. **Discord messages not sending**:
   - Verify webhook URL is correct
   - Check Discord server permissions
   - Test webhook with a simple curl command

### Log Files
The system creates detailed logs in `tts_queue.log`. Check this file for error messages and debugging information.

### Database Issues
The system uses SQLite for persistence. If you encounter database issues:
1. Delete `tts_queue.db` to reset
2. Check file permissions
3. Verify SQLite is available

## Customization

### Adding New Endpoints
You can extend the Flask app to add new functionality:

```python
@self.app.route('/custom_endpoint', methods=['POST'])
def custom_function():
    # Your custom logic here
    return jsonify({'status': 'success'})
```

### Modifying TTS Voice
Edit the TTS configuration in config.json:
- Change language (e.g., "en-gb", "en-au")
- Adjust speed with "slow": true
- Modify volume (0.0 to 1.0)

### Custom OBS Updates
You can modify the `update_queue_display` method to show different information or update multiple text sources.

## Security Considerations

1. **Local Network Only**: The system runs on localhost by default
2. **Firewall**: Ensure only necessary ports are open
3. **Authentication**: Consider adding API keys for production use
4. **Input Validation**: The system validates donation inputs

## Performance Tips

1. **Audio Quality**: Higher quality TTS may increase processing time
2. **Database**: Regular cleanup of old processed items
3. **Logging**: Adjust log levels for production use
4. **Resource Usage**: Monitor CPU usage during TTS generation

## Backup and Recovery

1. **Database**: Regularly backup `tts_queue.db`
2. **Configuration**: Keep a backup of your `config.json`
3. **Logs**: Archive log files periodically

The system is designed to be self-recovering and will handle most errors gracefully, but regular maintenance is recommended for optimal performance.

