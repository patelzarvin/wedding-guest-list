const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const express = require('express');
const cors = require('cors');
const qrcode = require('qrcode');
const qrcodeTerminal = require('qrcode-terminal');
const path = require('path');
const fs = require('fs');

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.BAILEYS_PORT || 5001;
const FLASK_PORT = process.env.FLASK_PORT || 5000;
const FLASK_WEBHOOK_URL = process.env.FLASK_WEBHOOK_URL || `http://127.0.0.1:${FLASK_PORT}/api/baileys-webhook`;
const AUTH_DIR = path.join(__dirname, 'auth_info');

if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
}

let sock = null;
let connectionStatus = 'initializing'; // initializing, qr, connecting, connected, disconnected
let qrDataUrl = null;
let currentUser = null;

// Clean phone number: 919825112233@s.whatsapp.net -> 9825112233
function cleanPhoneNumber(jid) {
    if (!jid) return '';
    let num = jid.split('@')[0];
    if (num.startsWith('91') && num.length === 12) {
        return num.substring(2);
    }
    return num;
}

function formatJid(phone) {
    let clean = phone.replace(/\D/g, '');
    if (clean.length === 10) {
        clean = '91' + clean;
    }
    return `${clean}@s.whatsapp.net`;
}

async function startWhatsApp() {
    try {
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
        const { version, isLatest } = await fetchLatestBaileysVersion();
        console.log(`[*] Starting WhatsApp socket (Baileys v${version.join('.')}, isLatest: ${isLatest})...`);

        sock = makeWASocket({
            version,
            logger: pino({ level: 'silent' }),
            auth: state,
            browser: ['Gujarati Wedding Bot', 'Chrome', '1.0.0'],
            connectTimeoutMs: 60000,
            defaultQueryTimeoutMs: 60000
        });

        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                connectionStatus = 'qr';
                try {
                    qrDataUrl = await qrcode.toDataURL(qr, { scale: 7, margin: 2 });
                } catch (e) {
                    console.error('[!] Error generating QR data URL:', e);
                }
                console.log('\n=============================================================');
                console.log('[*] NEW WHATSAPP QR CODE GENERATED. SCAN FROM YOUR PHONE:');
                console.log('=============================================================');
                try {
                    qrcodeTerminal.generate(qr, { small: true });
                } catch(e) {}
                console.log('👉 Or view and scan live on dashboard: http://127.0.0.1:5000\n');
            }

            if (connection === 'connecting') {
                if (connectionStatus !== 'qr') {
                    connectionStatus = 'connecting';
                }
            } else if (connection === 'close') {
                const statusCode = lastDisconnect?.error?.output?.statusCode;
                const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
                console.log(`[!] WhatsApp connection closed (${statusCode || 'Unknown'}). Reconnecting: ${shouldReconnect}`);

                connectionStatus = 'disconnected';
                qrDataUrl = null;
                currentUser = null;

                if (statusCode === DisconnectReason.loggedOut) {
                    console.log('[*] Logged out. Clearing credentials...');
                    try {
                        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                        fs.mkdirSync(AUTH_DIR, { recursive: true });
                    } catch (err) {}
                }

                if (shouldReconnect) {
                    setTimeout(startWhatsApp, 3000);
                } else {
                    setTimeout(startWhatsApp, 2000);
                }
            } else if (connection === 'open') {
                connectionStatus = 'connected';
                qrDataUrl = null;
                currentUser = sock?.user || null;
                console.log('\n=============================================================');
                console.log(`[+] SUCCESS! WHATSAPP CONNECTED AS: ${currentUser?.id || 'User'} (${currentUser?.name || 'Host'})`);
                console.log('=============================================================\n');
            }
        });

        // Listen for incoming WhatsApp messages
        sock.ev.on('messages.upsert', async ({ messages, type }) => {
            if (type !== 'notify') return;

            for (const msg of messages) {
                // Ignore outgoing messages sent by the bot itself
                if (msg.key.fromMe) continue;

                const remoteJid = msg.key.remoteJid;
                // Ignore broadcast / status / group messages
                if (!remoteJid || remoteJid.includes('@g.us') || remoteJid.includes('broadcast')) continue;

                const text = msg.message?.conversation ||
                             msg.message?.extendedTextMessage?.text ||
                             msg.message?.imageMessage?.caption ||
                             '';

                if (!text || text.trim() === '') continue;

                const senderPhone = cleanPhoneNumber(remoteJid);
                console.log(`\n[📩 WhatsApp Inbound] From: ${senderPhone} (${remoteJid}): "${text}"`);

                // Forward to Flask backend webhook
                try {
                    const response = await fetch(FLASK_WEBHOOK_URL, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            sender: senderPhone,
                            raw_jid: remoteJid,
                            remote_jid_alt: msg.key?.remoteJidAlt || '',
                            participant: msg.key?.participant || msg.key?.participantAlt || '',
                            push_name: msg.pushName || '',
                            message: text
                        })
                    });

                    if (response.ok) {
                        const data = await response.json();
                        if (data && data.reply && data.auto_send) {
                            console.log(`[🤖 AI Bot Reply to ${remoteJid}]: "${data.reply}"`);
                            // Send reply back to sender on WhatsApp
                            await sock.sendMessage(remoteJid, { text: data.reply });
                        }
                    } else {
                        console.error(`[!] Flask webhook responded with HTTP ${response.status}`);
                    }
                } catch (err) {
                    console.error('[!] Failed to call Flask webhook:', err.message);
                }
            }
        });

    } catch (err) {
        console.error('[!] Error in startWhatsApp:', err);
        connectionStatus = 'disconnected';
        setTimeout(startWhatsApp, 5000);
    }
}

// REST Endpoints for Flask Dashboard
app.get('/status', (req, res) => {
    res.json({
        status: connectionStatus,
        qr: qrDataUrl,
        user: currentUser
    });
});

app.post('/send', async (req, res) => {
    const { to, message } = req.body;
    if (!to || !message) {
        return res.status(400).json({ success: false, error: 'Missing "to" or "message"' });
    }

    if (connectionStatus !== 'connected' || !sock || !sock.user) {
        return res.status(503).json({
            success: false,
            error: 'WhatsApp is not linked yet. Please scan the QR code first.',
            not_connected: true
        });
    }

    try {
        let clean = to.toString().replace(/\D/g, '');
        if (clean.length === 10) {
            clean = '91' + clean;
        }
        let targetJid = `${clean}@s.whatsapp.net`;

        // Check if number is registered on WhatsApp using onWhatsApp
        try {
            if (sock && typeof sock.onWhatsApp === 'function') {
                const results = await sock.onWhatsApp(targetJid);
                if (results && results.length > 0 && results[0].exists && results[0].jid) {
                    targetJid = results[0].jid;
                }
            }
        } catch (onWaErr) {
            console.log('[!] onWhatsApp verification warning:', onWaErr.message);
        }

        const result = await sock.sendMessage(targetJid, { text: message });
        console.log(`[📤 Sent WhatsApp] To: ${to} (${targetJid})`);
        res.json({ success: true, messageId: result.key.id, jid: targetJid });
    } catch (err) {
        console.error('[!] Failed to send WhatsApp message:', err);
        res.status(500).json({ success: false, error: err.message });
    }
});

app.post('/disconnect', async (req, res) => {
    try {
        if (sock) {
            try { await sock.logout(); } catch(e) {}
        }
        try {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true });
            fs.mkdirSync(AUTH_DIR, { recursive: true });
        } catch (e) {}

        connectionStatus = 'disconnected';
        qrDataUrl = null;
        currentUser = null;
        res.json({ success: true, message: 'Disconnected. Ready for new QR scan.' });
        setTimeout(startWhatsApp, 1500);
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.listen(PORT, () => {
    console.log(`=============================================================`);
    console.log(`[*] Baileys WhatsApp Microservice running on http://127.0.0.1:${PORT}`);
    console.log(`=============================================================`);
    startWhatsApp();
});
