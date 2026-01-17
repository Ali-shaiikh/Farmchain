const express = require('express');
const router = express.Router();
const axios = require('axios');

// Translation API configurations
const TRANSLATION_CONFIGS = {
    google: {
        url: 'https://translation.googleapis.com/language/translate/v2',
        requiresKey: true,
        costPerMillionChars: 20 // USD
    },
    libre: {
        url: 'https://libretranslate.de/translate',
        requiresKey: false,
        costPerMillionChars: 0 // Free
    },
    deepl: {
        url: 'https://api-free.deepl.com/v2/translate',
        requiresKey: true,
        costPerMillionChars: 5.49 // EUR
    }
};

// Get translation configuration
router.get('/config', (req, res) => {
    const provider = process.env.TRANSLATION_PROVIDER || 'libre';
    const apiKey = process.env[`${provider.toUpperCase()}_API_KEY`];
    
    res.json({
        provider,
        apiKey: provider === 'libre' ? null : apiKey, // LibreTranslate doesn't require API key for public instances
        availableProviders: Object.keys(TRANSLATION_CONFIGS),
        costs: TRANSLATION_CONFIGS
    });
});

// Proxy translation requests (for security - API keys stay on server)
router.post('/translate', async (req, res) => {
    try {
        const { text, fromLang, toLang, provider = 'libre' } = req.body;
        
        if (!text || !fromLang || !toLang) {
            return res.status(400).json({ error: 'Missing required parameters' });
        }

        const config = TRANSLATION_CONFIGS[provider];
        if (!config) {
            return res.status(400).json({ error: 'Unsupported translation provider' });
        }

        let translatedText;
        
        switch (provider) {
            case 'google':
                translatedText = await translateWithGoogle(text, fromLang, toLang);
                break;
            case 'libre':
                translatedText = await translateWithLibreTranslate(text, fromLang, toLang);
                break;
            case 'deepl':
                translatedText = await translateWithDeepL(text, fromLang, toLang);
                break;
            default:
                return res.status(400).json({ error: 'Unsupported provider' });
        }

        res.json({ translatedText });
        
    } catch (error) {
        console.error('Translation error:', error);
        res.status(500).json({ error: 'Translation failed', details: error.message });
    }
});

// Google Translate API
async function translateWithGoogle(text, fromLang, toLang) {
    const apiKey = process.env.GOOGLE_API_KEY;
    if (!apiKey) {
        throw new Error('Google Translate API key not configured');
    }

    const params = new URLSearchParams({
        q: text,
        source: fromLang,
        target: toLang,
        key: apiKey
    });

    const response = await axios.post(`${TRANSLATION_CONFIGS.google.url}?${params}`, {}, {
        headers: {
            'Content-Type': 'application/json'
        }
    });

    return response.data.data.translations[0].translatedText;
}

// LibreTranslate API
async function translateWithLibreTranslate(text, fromLang, toLang) {
    const response = await axios.post(TRANSLATION_CONFIGS.libre.url, {
        q: text,
        source: fromLang,
        target: toLang,
        api_key: process.env.LIBRE_API_KEY // Optional
    }, {
        headers: {
            'Content-Type': 'application/json'
        }
    });

    return response.data.translatedText;
}

// DeepL API
async function translateWithDeepL(text, fromLang, toLang) {
    const apiKey = process.env.DEEPL_API_KEY;
    if (!apiKey) {
        throw new Error('DeepL API key not configured');
    }

    const response = await axios.post(TRANSLATION_CONFIGS.deepl.url, 
        new URLSearchParams({
            text: text,
            source_lang: fromLang.toUpperCase(),
            target_lang: toLang.toUpperCase()
        }), {
        headers: {
            'Authorization': `DeepL-Auth-Key ${apiKey}`,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    });

    return response.data.translations[0].text;
}

// Batch translation endpoint
router.post('/translate/batch', async (req, res) => {
    try {
        const { texts, fromLang, toLang, provider = 'libre' } = req.body;
        
        if (!texts || !Array.isArray(texts) || !fromLang || !toLang) {
            return res.status(400).json({ error: 'Missing required parameters' });
        }

        const results = [];
        
        for (const text of texts) {
            try {
                const translatedText = await translateText(text, fromLang, toLang, provider);
                results.push({ original: text, translated: translatedText, success: true });
            } catch (error) {
                results.push({ original: text, translated: text, success: false, error: error.message });
            }
        }

        res.json({ results });
        
    } catch (error) {
        console.error('Batch translation error:', error);
        res.status(500).json({ error: 'Batch translation failed', details: error.message });
    }
});

// Helper function for translation
async function translateText(text, fromLang, toLang, provider) {
    switch (provider) {
        case 'google':
            return await translateWithGoogle(text, fromLang, toLang);
        case 'libre':
            return await translateWithLibreTranslate(text, fromLang, toLang);
        case 'deepl':
            return await translateWithDeepL(text, fromLang, toLang);
        default:
            throw new Error('Unsupported provider');
    }
}

// Get translation statistics
router.get('/stats', (req, res) => {
    // This would typically come from a database
    // For now, return mock data
    res.json({
        totalTranslations: 0,
        charactersTranslated: 0,
        estimatedCost: 0,
        provider: process.env.TRANSLATION_PROVIDER || 'libre'
    });
});

// Health check for translation services
router.get('/health', async (req, res) => {
    const provider = process.env.TRANSLATION_PROVIDER || 'libre';
    const healthStatus = {};

    try {
        // Test translation with a simple text
        const testText = 'Hello';
        const translated = await translateText(testText, 'en', 'mr', provider);
        
        healthStatus[provider] = {
            status: 'healthy',
            responseTime: Date.now(),
            testTranslation: translated
        };
    } catch (error) {
        healthStatus[provider] = {
            status: 'unhealthy',
            error: error.message
        };
    }

    res.json(healthStatus);
});

module.exports = router;

