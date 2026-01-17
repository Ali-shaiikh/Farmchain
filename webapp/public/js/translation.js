class TranslationService {
    constructor() {
        this.currentLanguage = 'en';
        this.targetLanguage = 'mr';
        this.translationCache = new Map();
        this.apiKey = null; // Will be set from environment
        this.translationProvider = 'google'; // 'google', 'libre', 'deepl'
    }

    // Initialize translation service
    async init(provider = 'google', apiKey = null) {
        this.translationProvider = provider;
        this.apiKey = apiKey;
        
        // Load cached translations from localStorage
        this.loadCache();
        
        console.log(`Translation service initialized with ${provider}`);
    }

    // Set target language for translations
    setTargetLanguage(lang) {
        this.targetLanguage = lang;
    }

    // Main translation function
    async translateText(text, fromLang = 'en', toLang = null) {
        if (!text || text.trim() === '') return text;
        
        const targetLang = toLang || this.targetLanguage;
        
        // Check cache first
        const cacheKey = `${text}_${fromLang}_${targetLang}`;
        if (this.translationCache.has(cacheKey)) {
            return this.translationCache.get(cacheKey);
        }

        try {
            let translatedText;
            
            switch (this.translationProvider) {
                case 'google':
                    translatedText = await this.translateWithGoogle(text, fromLang, targetLang);
                    break;
                case 'libre':
                    translatedText = await this.translateWithLibreTranslate(text, fromLang, targetLang);
                    break;
                case 'deepl':
                    translatedText = await this.translateWithDeepL(text, fromLang, targetLang);
                    break;
                default:
                    throw new Error(`Unsupported translation provider: ${this.translationProvider}`);
            }

            // Cache the result
            this.translationCache.set(cacheKey, translatedText);
            this.saveCache();
            
            return translatedText;
        } catch (error) {
            console.error('Translation error:', error);
            return text; // Return original text if translation fails
        }
    }

    // Google Translate API
    async translateWithGoogle(text, fromLang, toLang) {
        const url = 'https://translation.googleapis.com/language/translate/v2';
        const params = new URLSearchParams({
            q: text,
            source: fromLang,
            target: toLang,
            key: this.apiKey
        });

        const response = await fetch(`${url}?${params}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error(`Google Translate API error: ${response.status}`);
        }

        const data = await response.json();
        return data.data.translations[0].translatedText;
    }

    // LibreTranslate (Open Source)
    async translateWithLibreTranslate(text, fromLang, toLang) {
        // You can use public instances or self-hosted
        const baseUrl = 'https://libretranslate.de/translate'; // Public instance
        
        const response = await fetch(baseUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                q: text,
                source: fromLang,
                target: toLang,
                api_key: this.apiKey // Optional for public instances
            })
        });

        if (!response.ok) {
            throw new Error(`LibreTranslate API error: ${response.status}`);
        }

        const data = await response.json();
        return data.translatedText;
    }

    // DeepL API
    async translateWithDeepL(text, fromLang, toLang) {
        const url = 'https://api-free.deepl.com/v2/translate';
        
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': `DeepL-Auth-Key ${this.apiKey}`,
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams({
                text: text,
                source_lang: fromLang.toUpperCase(),
                target_lang: toLang.toUpperCase()
            })
        });

        if (!response.ok) {
            throw new Error(`DeepL API error: ${response.status}`);
        }

        const data = await response.json();
        return data.translations[0].text;
    }

    // Batch translate multiple texts
    async translateBatch(texts, fromLang = 'en', toLang = null) {
        const targetLang = toLang || this.targetLanguage;
        const results = [];

        for (const text of texts) {
            const translated = await this.translateText(text, fromLang, targetLang);
            results.push(translated);
        }

        return results;
    }

    // Translate dynamic content (user-generated)
    async translateDynamicContent(container) {
        const textNodes = this.getTextNodes(container);
        
        for (const node of textNodes) {
            if (node.textContent.trim() && !this.isAlreadyTranslated(node)) {
                const originalText = node.textContent.trim();
                const translatedText = await this.translateText(originalText);
                
                // Store original text for reverting
                node.setAttribute('data-original-text', originalText);
                node.textContent = translatedText;
            }
        }
    }

    // Get all text nodes in a container
    getTextNodes(container) {
        const textNodes = [];
        const walker = document.createTreeWalker(
            container,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: (node) => {
                    // Skip script and style tags
                    if (node.parentElement.tagName === 'SCRIPT' || 
                        node.parentElement.tagName === 'STYLE') {
                        return NodeFilter.FILTER_REJECT;
                    }
                    // Only accept nodes with actual text content
                    return node.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
                }
            }
        );

        let node;
        while (node = walker.nextNode()) {
            textNodes.push(node);
        }

        return textNodes;
    }

    // Check if text is already translated
    isAlreadyTranslated(node) {
        return node.hasAttribute('data-original-text') || 
               node.parentElement.hasAttribute('data-translated');
    }

    // Revert translations
    revertTranslations(container) {
        const translatedNodes = container.querySelectorAll('[data-original-text]');
        
        translatedNodes.forEach(node => {
            const originalText = node.getAttribute('data-original-text');
            node.textContent = originalText;
            node.removeAttribute('data-original-text');
        });
    }

    // Cache management
    saveCache() {
        try {
            localStorage.setItem('translationCache', JSON.stringify(Array.from(this.translationCache.entries())));
        } catch (error) {
            console.warn('Could not save translation cache:', error);
        }
    }

    loadCache() {
        try {
            const cached = localStorage.getItem('translationCache');
            if (cached) {
                this.translationCache = new Map(JSON.parse(cached));
            }
        } catch (error) {
            console.warn('Could not load translation cache:', error);
        }
    }

    clearCache() {
        this.translationCache.clear();
        localStorage.removeItem('translationCache');
    }

    // Get translation statistics
    getStats() {
        return {
            cacheSize: this.translationCache.size,
            provider: this.translationProvider,
            targetLanguage: this.targetLanguage
        };
    }
}

// Global translation service instance
window.TranslationService = new TranslationService();

