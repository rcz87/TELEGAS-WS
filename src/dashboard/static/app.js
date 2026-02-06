// Dashboard JavaScript - Alpine.js Component
// Handles WebSocket communication, API calls, and UI interactions

function dashboard() {
    return {
        // State
        stats: {
            messages_received: 0,
            messages_processed: 0,
            signals_generated: 0,
            alerts_sent: 0,
            errors: 0,
            uptime_seconds: 0
        },
        coins: [],
        signals: [],
        newCoin: '',
        isConnected: false,
        toast: {
            show: false,
            emoji: '',
            title: '',
            message: ''
        },
        ws: null,
        apiToken: '',

        // Auth helper: build headers with token if configured
        authHeaders(extra = {}) {
            const headers = {...extra};
            if (this.apiToken) {
                headers['Authorization'] = `Bearer ${this.apiToken}`;
            }
            return headers;
        },

        // Auth helper: fetch with auto-auth
        authFetch(url, options = {}) {
            options.headers = this.authHeaders(options.headers || {});
            return fetch(url, options);
        },

        // Initialize
        init() {
            console.log('🚀 Initializing TELEGLAS Dashboard...');
            // Read token from meta tag or prompt
            const meta = document.querySelector('meta[name="api-token"]');
            this.apiToken = meta ? meta.content : '';
            this.loadInitialData();
            this.connectWebSocket();
            this.startPeriodicRefresh();
        },

        // WebSocket Connection
        connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            let wsUrl = `${protocol}//${window.location.host}/ws`;
            if (this.apiToken) {
                wsUrl += `?token=${this.apiToken}`;
            }

            console.log('Connecting to WebSocket:', wsUrl);
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                this.isConnected = true;
                console.log('✅ WebSocket connected');
            };
            
            this.ws.onmessage = (event) => {
                const message = JSON.parse(event.data);
                this.handleWebSocketMessage(message);
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.isConnected = false;
            };
            
            this.ws.onclose = () => {
                this.isConnected = false;
                console.log('WebSocket disconnected, reconnecting in 3s...');
                setTimeout(() => this.connectWebSocket(), 3000);
            };
        },

        handleWebSocketMessage(message) {
            console.log('Received:', message.type);
            
            switch (message.type) {
                case 'initial_state':
                    this.stats = message.data.stats || this.stats;
                    this.coins = message.data.coins || [];
                    this.signals = message.data.signals || [];
                    break;
                
                case 'stats_update':
                    this.stats = message.data;
                    break;
                
                case 'order_flow_update':
                    this.updateCoinOrderFlow(message.data);
                    break;
                
                case 'new_signal':
                    this.addNewSignal(message.data);
                    break;
                
                case 'coin_added':
                    this.coins.push(message.data);
                    break;
                
                case 'coin_removed':
                    this.coins = this.coins.filter(c => c.symbol !== message.data.symbol);
                    break;
                
                case 'coin_toggled':
                    const coin = this.coins.find(c => c.symbol === message.data.symbol);
                    if (coin) {
                        coin.active = message.data.active;
                    }
                    break;
            }
        },

        // Load initial data
        async loadInitialData() {
            try {
                const [statsRes, coinsRes, signalsRes] = await Promise.all([
                    this.authFetch('/api/stats'),
                    this.authFetch('/api/coins'),
                    this.authFetch('/api/signals')
                ]);
                
                this.stats = await statsRes.json();
                const coinsData = await coinsRes.json();
                const signalsData = await signalsRes.json();
                
                this.coins = coinsData.coins || [];
                this.signals = signalsData.signals || [];
                
                console.log('✅ Data loaded:', {
                    coins: this.coins.length,
                    signals: this.signals.length
                });
            } catch (error) {
                console.error('Error loading data:', error);
            }
        },

        // Periodic refresh (fallback if WebSocket disconnected)
        startPeriodicRefresh() {
            setInterval(async () => {
                if (!this.isConnected) {
                    await this.loadInitialData();
                }
            }, 10000); // Every 10 seconds
        },

        // Coin Management
        async addCoin() {
            if (!this.newCoin) return;
            
            const symbol = this.newCoin.toUpperCase().trim();
            
            try {
                const response = await this.authFetch('/api/coins/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({symbol: symbol})
                });
                
                if (response.ok) {
                    this.newCoin = '';
                    this.showToast('✅', 'Coin Added', `${symbol} is now monitored`);
                } else {
                    const error = await response.json();
                    this.showToast('❌', 'Error', error.detail || 'Failed to add coin');
                }
            } catch (error) {
                console.error('Error adding coin:', error);
                this.showToast('❌', 'Error', 'Network error');
            }
        },

        async toggleCoin(symbol, active) {
            try {
                await this.authFetch(`/api/coins/${symbol}/toggle`, {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({active: active})
                });
                
                const action = active ? 'enabled' : 'paused';
                this.showToast('🔄', 'Coin Updated', `Alerts ${action} for ${symbol}`);
            } catch (error) {
                console.error('Error toggling coin:', error);
            }
        },

        async removeCoin(symbol) {
            if (!confirm(`Remove ${symbol} from monitoring?`)) return;
            
            try {
                await this.authFetch(`/api/coins/remove/${symbol}`, {
                    method: 'DELETE'
                });
                
                this.showToast('🗑️', 'Coin Removed', `${symbol} removed`);
            } catch (error) {
                console.error('Error removing coin:', error);
            }
        },

        // Update order flow
        updateCoinOrderFlow(data) {
            const coin = this.coins.find(c => c.symbol === data.symbol);
            if (coin) {
                coin.buy_ratio = data.buy_ratio || 0;
                coin.sell_ratio = data.sell_ratio || 0;
                coin.large_buys = data.large_buys || 0;
                coin.large_sells = data.large_sells || 0;
                coin.last_update = 'just now';
            }
        },

        // Add new signal
        addNewSignal(signal) {
            this.signals.unshift(signal);
            
            // Keep only last 200
            if (this.signals.length > 200) {
                this.signals = this.signals.slice(0, 200);
            }
            
            // Show notification
            this.showToast(
                '⚡',
                signal.symbol + ' Signal',
                `${(signal.type || '').replace(/_/g, ' ')} - ${signal.confidence}%`
            );
        },

        // UI Helpers
        formatNumber(num) {
            if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
            if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
            return num.toString();
        },

        formatUptime(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            return `${hours}h ${minutes}m`;
        },

        // Toast notifications
        showToast(emoji, title, message) {
            this.toast = {
                show: true,
                emoji: emoji,
                title: title,
                message: message
            };
            
            setTimeout(() => {
                this.toast.show = false;
            }, 3000);
        }
    }
}
