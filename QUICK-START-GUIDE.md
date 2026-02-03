# TELEGLAS Pro - Quick Start Guide

## 🚀 For Team Lead / Project Manager

### 1. First Actions (Day 1)
```bash
# Download this entire folder
# Upload to your VPS or share with team

# Review these files in order:
1. 00-PROJECT-OVERVIEW.md  (10 min read)
2. 01-COMPLETE-BLUEPRINT.md (30 min read)
3. This file for immediate actions
```

### 2. Team Assignments
- **Backend Developer:** Read blueprint Phase 1-3, start environment setup
- **DevOps:** Read 05-DEPLOYMENT-GUIDE.md, prepare VPS
- **Trader/PO:** Read 04-CONFIGURATION-GUIDE.md, define parameters
- **QA:** Read 06-TESTING-PROTOCOL.md, prepare test scenarios

### 3. Prerequisites Verification
```bash
# Run this on your VPS:
bash scripts/check-prerequisites.sh
```

---

## 💻 For Developer - Quick Setup

### Step 1: Clone/Upload to VPS
```bash
# SSH to VPS
ssh root@your_vps_ip

# Upload this folder
# Or git clone if in repo

cd teleglas-pro
```

### Step 2: Run Setup Script
```bash
# Make executable
chmod +x scripts/setup.sh

# Run setup
./scripts/setup.sh
```

This will:
- Check system requirements
- Install dependencies
- Create directory structure
- Setup virtual environment
- Prepare configuration files

### Step 3: Configure
```bash
# Edit configuration
nano config/config.yaml

# Add your API keys
nano config/secrets.env
```

### Step 4: Test Connection
```bash
# Activate venv
source venv/bin/activate

# Test WebSocket
python scripts/test_websocket.py
```

### Step 5: Start Service
```bash
# Start with PM2
pm2 start ecosystem.config.js

# Check status
pm2 status

# View logs
pm2 logs teleglas
```

---

## 🎯 For Trader - Configuration

### Essential Settings to Configure

**1. Trading Pairs (config/config.yaml)**
```yaml
pairs:
  primary:
    - BTCUSDT
    - ETHUSDT
    # Add your pairs here
```

**2. Alert Thresholds**
```yaml
thresholds:
  liquidation_cascade_threshold: 2000000  # Adjust based on your needs
  large_order_btc_eth: 100000
  large_order_altcoin: 10000
```

**3. Telegram Alerts**
```yaml
telegram:
  alert_types:
    urgent: true   # High confidence (85%+)
    watch: true    # Medium confidence (70%+)
    info: false    # Low confidence (60%+)
```

### Testing Your Configuration
```bash
# Test with paper trading mode
python scripts/paper_trading_test.py --duration 3600  # 1 hour test
```

---

## 📊 For QA - Testing Checklist

### Phase 1 Testing (Week 1)
```
[ ] Connection established
[ ] Ping/pong working
[ ] Reconnection after disconnect
[ ] No memory leaks (24h test)
[ ] Logs are clear
```

### Phase 2 Testing (Week 2)
```
[ ] Stop hunt detection accuracy
[ ] Order flow calculations correct
[ ] Event detection timing
[ ] No false positives spike
```

### Phase 3 Testing (Week 3-4)
```
[ ] Telegram alerts deliver
[ ] Alert formatting correct
[ ] Confidence scores reasonable
[ ] No alert spam
```

### Performance Testing
```bash
# Run load test
python tests/load_test.py

# Check memory usage
python tests/memory_profile.py

# Benchmark latency
python tests/latency_benchmark.py
```

---

## 🔧 For DevOps - Deployment

### VPS Requirements
- **OS:** Ubuntu 22.04 LTS (recommended)
- **RAM:** 2GB minimum, 4GB recommended
- **CPU:** 2 cores minimum
- **Storage:** 10GB minimum
- **Network:** Stable, 99%+ uptime

### Quick Deployment
```bash
# 1. Run deployment script
./scripts/deploy.sh

# 2. Configure PM2 for auto-start
pm2 startup
pm2 save

# 3. Setup monitoring
./scripts/setup-monitoring.sh

# 4. Configure alerts
./scripts/setup-health-alerts.sh
```

### Monitoring Dashboard
```bash
# Access PM2 web dashboard
pm2 web

# Or use monitoring script
./scripts/monitor.sh
```

---

## 🆘 Troubleshooting

### Connection Issues
```bash
# Check network
ping open-ws.coinglass.com

# Test API key
curl -H "CG-API-KEY: your_key" \
  https://open-api-v4.coinglass.com/api/futures/supported-coins

# Check logs
tail -f logs/teleglas.log | grep ERROR
```

### No Alerts Received
```bash
# Test Telegram bot
python scripts/test_telegram.py

# Check alert settings
cat config/config.yaml | grep telegram -A 10

# Verify message queue
python scripts/check_queue.py
```

### Performance Issues
```bash
# Check resource usage
./scripts/status.sh

# Analyze memory
python scripts/memory_check.py

# Profile performance
python scripts/profile.py
```

---

## 📞 Getting Help

### Documentation
- **Full Blueprint:** 01-COMPLETE-BLUEPRINT.md
- **Architecture:** 02-ARCHITECTURE.md
- **API Reference:** 03-API-REFERENCE.md
- **Configuration:** 04-CONFIGURATION-GUIDE.md
- **Deployment:** 05-DEPLOYMENT-GUIDE.md
- **Testing:** 06-TESTING-PROTOCOL.md

### Common Issues
See TROUBLESHOOTING.md

### Support Channels
[Add your team's communication channels]

---

## ✅ Success Checklist

### Week 1 Goals
```
[ ] Environment setup complete
[ ] WebSocket connecting
[ ] Receiving liquidation data
[ ] Logs working
[ ] Team aligned
```

### Week 2 Goals
```
[ ] All analyzers implemented
[ ] Unit tests passing
[ ] Integration tests working
[ ] Code review complete
```

### Week 3-4 Goals
```
[ ] Telegram alerts working
[ ] System deployed to VPS
[ ] Monitoring active
[ ] Paper trading started
```

### Week 5-6 Goals
```
[ ] Real trading validation
[ ] Performance optimized
[ ] Documentation complete
[ ] Handoff ready
```

---

## 🎯 Next Steps

1. **Immediate (Today):**
   - Read project overview
   - Assign team roles
   - Verify VPS access
   - Check CoinGlass API access

2. **This Week:**
   - Complete Phase 1 (Foundation)
   - Test WebSocket connection
   - Validate configuration
   - Setup monitoring

3. **Next 2 Weeks:**
   - Implement analyzers
   - Build alert system
   - Deploy to VPS
   - Start paper trading

4. **Month 2:**
   - Optimize thresholds
   - Validate win rate improvement
   - Full production rollout
   - Monitor and iterate

---

**Questions? Check the FAQ section in 01-COMPLETE-BLUEPRINT.md**

**Ready to start? Run: `./scripts/setup.sh`**
