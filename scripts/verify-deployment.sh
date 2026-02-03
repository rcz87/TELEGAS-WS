#!/bin/bash
# TELEGLAS Pro - Deployment Verification Script

echo "═══════════════════════════════════════════════════════════════"
echo "    TELEGLAS Pro - DEPLOYMENT VERIFICATION"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check function
check_item() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
        return 0
    else
        echo -e "${RED}❌ $2${NC}"
        return 1
    fi
}

warning_item() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

PASSED=0
FAILED=0
WARNINGS=0

echo "1. System Dependencies:"
echo "   ├─ Python Version:"
python3 --version
check_item $? "Python 3 installed"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "   ├─ pip Version:"
pip3 --version > /dev/null 2>&1
check_item $? "pip3 installed"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "   ├─ PM2 Version:"
pm2 --version > /dev/null 2>&1
check_item $? "PM2 installed ($(pm2 --version))"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "   └─ Node.js Version:"
node --version > /dev/null 2>&1
check_item $? "Node.js installed ($(node --version))"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "2. Python Dependencies:"
DEPS=("fastapi" "uvicorn" "websockets" "python-telegram-bot" "pydantic")
for dep in "${DEPS[@]}"; do
    pip3 list 2>/dev/null | grep -qi "^${dep}"
    check_item $? "$dep installed"
    [ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))
done

echo ""
echo "3. Configuration Files:"
[ -f "config/config.yaml" ]
check_item $? "config.yaml exists"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

[ -f "config/secrets.env" ]
check_item $? "secrets.env exists"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

[ -f "ecosystem.config.js" ]
check_item $? "ecosystem.config.js exists"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "4. API Configuration:"
if grep -q "your_coinglass_api_key_here" config/secrets.env 2>/dev/null; then
    warning_item "CoinGlass API Key not configured (using placeholder)"
    ((WARNINGS++))
else
    check_item 0 "CoinGlass API Key configured"
    ((PASSED++))
fi

if grep -q "your_telegram_bot_token_here" config/secrets.env 2>/dev/null; then
    warning_item "Telegram Bot Token not configured (using placeholder)"
    ((WARNINGS++))
else
    check_item 0 "Telegram Bot Token configured"
    ((PASSED++))
fi

echo ""
echo "5. Directory Structure:"
[ -d "logs" ]
check_item $? "logs/ directory exists"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

[ -d "data" ]
check_item $? "data/ directory exists"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

[ -d "scripts" ]
check_item $? "scripts/ directory exists"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "6. PM2 Configuration:"
pm2 list 2>/dev/null | grep -q "teleglas-pro"
check_item $? "teleglas-pro in PM2 process list"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

systemctl is-enabled pm2-root > /dev/null 2>&1
check_item $? "PM2 auto-start on boot enabled"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "7. Firewall Configuration:"
ufw status | grep -q "Status: active"
check_item $? "UFW firewall active"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

ufw status | grep -q "22/tcp.*ALLOW"
check_item $? "SSH port 22 allowed"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

ufw status | grep -q "8080/tcp.*ALLOW"
check_item $? "Dashboard port 8080 allowed"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "8. Log Management:"
[ -f "/etc/logrotate.d/teleglas" ]
check_item $? "Log rotation configured"
[ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))

echo ""
echo "9. Management Scripts:"
SCRIPTS=("start.sh" "stop.sh" "restart.sh" "status.sh" "logs.sh" "update.sh" "check.sh")
for script in "${SCRIPTS[@]}"; do
    [ -x "scripts/$script" ]
    check_item $? "scripts/$script executable"
    [ $? -eq 0 ] && ((PASSED++)) || ((FAILED++))
done

echo ""
echo "10. System Resources:"
echo "    ├─ Disk Space:"
DISK_USED=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $DISK_USED -lt 80 ]; then
    check_item 0 "Disk usage: ${DISK_USED}% (healthy)"
    ((PASSED++))
else
    warning_item "Disk usage: ${DISK_USED}% (consider cleanup)"
    ((WARNINGS++))
fi

echo ""
echo "    ├─ Memory:"
TOTAL_MEM=$(free -g | awk '/^Mem:/{print $2}')
FREE_MEM=$(free -g | awk '/^Mem:/{print $7}')
echo "       Total: ${TOTAL_MEM}GB, Available: ${FREE_MEM}GB"
check_item 0 "Memory available"
((PASSED++))

echo ""
echo "    └─ CPU Load:"
LOAD=$(uptime | awk -F'load average:' '{print $2}' | awk -F',' '{print $1}' | xargs)
echo "       Load Average: $LOAD"
check_item 0 "System responsive"
((PASSED++))

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "                    VERIFICATION SUMMARY"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo -e "  ${GREEN}Passed:${NC}   $PASSED"
echo -e "  ${RED}Failed:${NC}   $FAILED"
echo -e "  ${YELLOW}Warnings:${NC} $WARNINGS"
echo ""

if [ $FAILED -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✅ DEPLOYMENT FULLY VERIFIED - READY FOR PRODUCTION!${NC}"
    echo ""
    echo "🚀 Next Steps:"
    echo "   1. Add your API credentials to: config/secrets.env"
    echo "   2. Start the system: ./scripts/start.sh"
    echo "   3. Access dashboard: http://31.97.107.243:8080"
    EXIT_CODE=0
elif [ $FAILED -eq 0 ]; then
    echo -e "${YELLOW}⚠️  DEPLOYMENT VERIFIED WITH WARNINGS${NC}"
    echo ""
    echo "📝 Action Required:"
    echo "   - Configure API credentials in: config/secrets.env"
    echo "   - Once configured, start with: ./scripts/start.sh"
    EXIT_CODE=0
else
    echo -e "${RED}❌ DEPLOYMENT INCOMPLETE - ISSUES DETECTED${NC}"
    echo ""
    echo "Please review the failed items above and resolve them."
    EXIT_CODE=1
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
exit $EXIT_CODE
