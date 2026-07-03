const puppeteer = require('puppeteer-core');
const path = require('path');
const fs = require('fs');

const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PROFILE_PATH = path.resolve(__dirname, '../.chrome_scraper_profile');

// Lecture des arguments
let headless = true;
process.argv.forEach(val => {
    if (val === '--headless=false') headless = false;
    if (val === '--headless=true') headless = true;
});

console.error(`[SCRAPER] Mode Headless: ${headless}`);
console.error(`[SCRAPER] Dossier de profil: ${PROFILE_PATH}`);

async function run() {
    let browser;
    let page;
    let isConnected = false;
    try {
        // Essayer de se connecter à une instance Chrome existante avec débogage distant
        try {
            console.error("[SCRAPER] Tentative de connexion à Chrome actif sur le port 9222...");
            browser = await puppeteer.connect({
                browserURL: 'http://127.0.0.1:9222'
            });
            console.error("[SCRAPER] Connecté avec succès à l'instance Chrome active !");
            isConnected = true;
        } catch (connectErr) {
            console.error("[SCRAPER] Pas d'instance Chrome active sur le port 9222. Fallback sur le profil isolé...");
            browser = await puppeteer.launch({
                executablePath: CHROME_PATH,
                headless: headless ? "new" : false,
                userDataDir: PROFILE_PATH,
                ignoreDefaultArgs: ['--enable-automation'],
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--window-size=1280,1024',
                    '--disable-notifications',
                    '--disable-blink-features=AutomationControlled'
                ],
                defaultViewport: null
            });
        }

        page = await browser.newPage();
        
        // Masquer la détection Puppeteer
        await page.evaluateOnNewDocument(() => {
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        });
        
        // Timeout global de navigation
        page.setDefaultNavigationTimeout(45000);

        let gcpResult = null;
        let claudeResult = null;
        let authRequired = false;

        // --- TÂCHE 1 : FACTURATION GCP ---
        try {
            console.error("[SCRAPER] [GCP] Navigation vers la console de facturation GCP...");
            await page.goto("https://console.cloud.google.com/billing", { waitUntil: 'domcontentloaded' });

            // Attente du chargement initial (8s) pour voir s'il y a redirection
            console.error("[SCRAPER] [GCP] Attente du chargement de la page et des redirections...");
            await new Promise(r => setTimeout(r, 8000));
            
            const currentUrl = page.url();
            console.error(`[SCRAPER] [GCP] URL actuelle après attente: ${currentUrl}`);

            // Détection de la page de login
            if (currentUrl.includes("accounts.google.com") || currentUrl.includes("signin")) {
                console.error("[SCRAPER] [GCP] Redirection de connexion Google détectée.");
                authRequired = true;
                if (headless && !isConnected) {
                    console.error("[SCRAPER] [GCP] ÉCHEC: Authentification Google requise et mode headless actif.");
                    process.exit(2);
                } else {
                    console.error("[SCRAPER] [GCP] En attente de la connexion par l'utilisateur (Timeout: 120s)...");
                    let loggedIn = false;
                    for (let i = 0; i < 120; i++) {
                        await new Promise(r => setTimeout(r, 1000));
                        const checkUrl = page.url();
                        if (checkUrl.includes("console.cloud.google.com/billing")) {
                            console.error("[SCRAPER] [GCP] Connexion détectée ! Poursuite...");
                            loggedIn = true;
                            break;
                        }
                    }
                    if (!loggedIn) {
                        console.error("[SCRAPER] [GCP] ÉCHEC: Timeout d'authentification.");
                        process.exit(3);
                    }
                    // Attente additionnelle après connexion
                    await new Promise(r => setTimeout(r, 5000));
                }
            }

            const bodyText = await page.evaluate(() => document.body.innerText);
            const lines = bodyText.split('\n').map(l => l.trim()).filter(Boolean);
            console.error(`[SCRAPER] [GCP] Analyse de ${lines.length} lignes de texte...`);

            const KEYWORDS = [
                "coût cumulé à ce jour", "coût cumulé", "charges à ce jour", "facturation à ce jour",
                "charges du mois à ce jour", "charges cumulées", "month-to-date", "charges to date",
                "accrued charges", "month to date cost", "total à ce jour",
                "dépenses des 30 derniers jours", "dépenses des 30", "dépenses", "my billing account",
                "billing account", "compte de facturation", "billing accounts"
            ];

            let foundCost = null;
            let foundCurrency = "USD";

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].toLowerCase();
                const matchesKeyword = KEYWORDS.some(kw => line.includes(kw));
                
                if (matchesKeyword) {
                    console.error(`[SCRAPER] [GCP] Ligne mot-clé trouvée (${i}): "${lines[i]}"`);
                    for (let offset = 0; offset <= 4; offset++) {
                        if (i + offset >= lines.length) break;
                        const textToSearch = lines[i + offset];
                        const priceRegex = /(?:[\$€£]\s*(\d+(?:[\.,]\d+)?))|(?:(\d+(?:[\.,]\d+)?)\s*[\$€£])|(?:(usd|eur)\s*(\d+(?:[\.,]\d+)?))|(?:(\d+(?:[\.,]\d+)?)\s*(usd|eur))/i;
                        const match = textToSearch.match(priceRegex);
                        
                        if (match) {
                            console.error(`[SCRAPER] [GCP] Prix trouvé offset +${offset}: "${textToSearch}"`);
                            const rawVal = match[1] || match[2] || match[4] || match[5];
                            if (rawVal) {
                                const cleanVal = parseFloat(rawVal.replace(',', '.'));
                                if (!isNaN(cleanVal)) {
                                    foundCost = cleanVal;
                                    const textLower = textToSearch.toLowerCase();
                                    if (textLower.includes('€') || textLower.includes('eur')) {
                                        foundCurrency = "EUR";
                                    } else if (textLower.includes('$') || textLower.includes('usd')) {
                                        foundCurrency = "USD";
                                    }
                                    break;
                                }
                            }
                        }
                    }
                }
                if (foundCost !== null) break;
            }

            if (foundCost !== null) {
                gcpResult = {
                    cost_usd: foundCurrency === "EUR" ? foundCost * 1.09 : foundCost,
                    cost_raw: foundCost,
                    currency: foundCurrency
                };
                console.error(`[SCRAPER] [GCP] Coût extrait: ${foundCost} ${foundCurrency}`);
            } else {
                console.error("[SCRAPER] [GCP] Aucun montant détecté sur la page.");
            }
        } catch (gcpErr) {
            console.error(`[SCRAPER] [GCP] Erreur globale: ${gcpErr.message}`);
        }

        // --- TÂCHE 2 : CLAUDE USAGE ---
        try {
            console.error("[SCRAPER] [CLAUDE] Navigation vers la page d'utilisation de Claude...");
            await page.goto("https://claude.ai/settings/usage", { waitUntil: 'domcontentloaded' });
            await new Promise(r => setTimeout(r, 6000));

            const currentUrl = page.url();
            console.error(`[SCRAPER] [CLAUDE] URL actuelle: ${currentUrl}`);

            if (!currentUrl.includes("signin") && !currentUrl.includes("login") && !currentUrl.includes("register")) {
                const bodyText = await page.evaluate(() => document.body.innerText);
                console.error(`[SCRAPER] [CLAUDE] Texte Claude extrait (${bodyText.length} char)`);

                let message_usage = null;
                
                // 1. Recherche de pourcentage d'utilisation (ex: "75% of limit" ou "20% restant")
                const pctMatch = bodyText.match(/(\d+)\s*%/);
                if (pctMatch) {
                    message_usage = parseInt(pctMatch[1]);
                    console.error(`[SCRAPER] [CLAUDE] Pourcentage trouvé via regex %%: ${message_usage}%`);
                } else {
                    // 2. Recherche de type X of Y messages (ex: "15 of 45 messages" ou "15 / 45")
                    const ratioMatch = bodyText.match(/(\d+)\s*(?:of|sur|\/)\s*(\d+)\s*(?:messages|msg|limit)?/i) ||
                                       bodyText.match(/(\d+)\s*(?:of|sur|\/)\s*(\d+)/i);
                    if (ratioMatch) {
                        const used = parseInt(ratioMatch[1]);
                        const limit = parseInt(ratioMatch[2]);
                        if (limit > 0 && used <= limit) {
                            message_usage = Math.round((used / limit) * 100);
                            console.error(`[SCRAPER] [CLAUDE] Pourcentage calculé via ratio (${used}/${limit}): ${message_usage}%`);
                        }
                    }
                }

                // Récupérer des lignes contenant des infos utiles
                const interestingLines = bodyText.split('\n')
                    .map(l => l.trim())
                    .filter(l => l.toLowerCase().includes('message') || l.toLowerCase().includes('limit') || l.toLowerCase().includes('quota') || l.toLowerCase().includes('%') || l.toLowerCase().includes('crédit') || l.toLowerCase().includes('usage'));

                const summaryText = interestingLines.slice(0, 4).join(" | ") || bodyText.substring(0, 150).replace(/\s+/g, ' ');
                claudeResult = {
                    message_usage_pct: message_usage,
                    summary_text: summaryText
                };
                console.error(`[SCRAPER] [CLAUDE] Synchro réussie: ${JSON.stringify(claudeResult)}`);
            } else {
                console.error("[SCRAPER] [CLAUDE] Non connecté à Claude.ai (redirection login).");
                authRequired = true;
            }
        } catch (claudeErr) {
            console.error(`[SCRAPER] [CLAUDE] Erreur globale: ${claudeErr.message}`);
        }

        // --- ENVOI DES RÉSULTATS ---
        if (gcpResult !== null || claudeResult !== null) {
            const finalResult = {
                status: "success",
                gcp: gcpResult,
                claude: claudeResult,
                timestamp: new Date().toISOString()
            };
            console.log(JSON.stringify(finalResult));
        } else {
            const finalResult = {
                status: "error",
                message: authRequired ? "Authentification requise pour Google Cloud ou Claude." : "Impossible d'extraire des données de GCP ou de Claude (vérifiez vos connexions Chrome)."
            };
            console.log(JSON.stringify(finalResult));
            process.exit(authRequired ? 2 : 1);
        }

    } catch (err) {
        console.error(`[SCRAPER] Erreur fatale: ${err.message}`);
        const result = {
            status: "error",
            message: err.message
        };
        console.log(JSON.stringify(result));
        process.exit(1);
    } finally {
        if (browser) {
            if (isConnected) {
                if (page) {
                    try {
                        await page.close();
                    } catch (e) {
                        console.error("[SCRAPER] Erreur lors de la fermeture de la page: " + e.message);
                    }
                }
                browser.disconnect();
                console.error("[SCRAPER] Déconnecté de l'instance Chrome active sans la fermer.");
            } else {
                await browser.close();
                console.error("[SCRAPER] Navigateur isolé fermé.");
            }
        }
    }
}

run();
