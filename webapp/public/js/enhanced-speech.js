let currentSpeech = null;
let isPaused = false;
let currentLanguage = 'en';
let translationEnabled = false;

// Initialize translation service
async function initTranslationService() {
    try {
        // Get API configuration from server
        const response = await fetch('/api/translation/config');
        const config = await response.json();
        
        await window.TranslationService.init(
            config.provider || 'libre', // Default to LibreTranslate (free)
            config.apiKey
        );
        
        translationEnabled = true;
        console.log('Translation service initialized successfully');
    } catch (error) {
        console.warn('Translation service not available, falling back to static translations');
        translationEnabled = false;
    }
}

function initSpeechSynthesis() {
    if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
    }
}

async function speakText(text, lang = null) {
    // Only allow TTS when Marathi is selected
    if (currentLanguage !== 'mr') {
        return;
    }
    
    if (currentSpeech) {
        window.speechSynthesis.cancel();
    }

    let textToSpeak = text;
    
    // If translation is enabled and text is in English, translate it
    if (translationEnabled && currentLanguage === 'mr' && lang === 'en') {
        try {
            textToSpeak = await window.TranslationService.translateText(text, 'en', 'mr');
        } catch (error) {
            console.warn('Translation failed, using original text:', error);
            textToSpeak = text;
        }
    }

    const utterance = new SpeechSynthesisUtterance(textToSpeak);
    
    // Use Marathi TTS
    utterance.lang = 'hi-IN';
    
    utterance.rate = 0.9;
    utterance.volume = 1;
    utterance.pitch = 1;

    utterance.onstart = () => {
        currentSpeech = utterance;
        isPaused = false;
        highlightSpeakingElement(text);
        updateSpeechControls();
    };

    utterance.onend = () => {
        currentSpeech = null;
        isPaused = false;
        removeSpeakingHighlight();
        updateSpeechControls();
    };

    utterance.onpause = () => {
        isPaused = true;
        updateSpeechControls();
    };

    utterance.onresume = () => {
        isPaused = false;
        updateSpeechControls();
    };

    window.speechSynthesis.speak(utterance);
}

function stopSpeech() {
    if (currentSpeech) {
        window.speechSynthesis.cancel();
        currentSpeech = null;
        isPaused = false;
        removeSpeakingHighlight();
        updateSpeechControls();
    }
}

function pauseSpeech() {
    if (currentSpeech && !isPaused) {
        window.speechSynthesis.pause();
    }
}

function resumeSpeech() {
    if (currentSpeech && isPaused) {
        window.speechSynthesis.resume();
    }
}

function updateSpeechControls() {
    const stopBtn = document.getElementById('stop-speech');
    const pauseBtn = document.getElementById('pause-speech');
    const resumeBtn = document.getElementById('resume-speech');

    // Only show speech controls when Marathi is selected
    if (currentLanguage === 'mr') {
        if (currentSpeech) {
            stopBtn.disabled = false;
            pauseBtn.disabled = isPaused;
            resumeBtn.disabled = !isPaused;
        } else {
            stopBtn.disabled = true;
            pauseBtn.disabled = true;
            resumeBtn.disabled = true;
        }
    } else {
        // Hide/disable speech controls when English is selected
        stopBtn.disabled = true;
        pauseBtn.disabled = true;
        resumeBtn.disabled = true;
    }
}

function highlightSpeakingElement(text) {
    removeSpeakingHighlight();
    const elements = document.querySelectorAll('.speakable');
    elements.forEach(element => {
        if (element.textContent.trim() === text.trim()) {
            element.classList.add('speaking');
        }
    });
}

function removeSpeakingHighlight() {
    const elements = document.querySelectorAll('.speaking');
    elements.forEach(element => {
        element.classList.remove('speaking');
    });
}

function addSpeechListeners() {
    const speakableElements = document.querySelectorAll('.speakable');
    
    speakableElements.forEach(element => {
        element.removeEventListener('click', handleSpeechClick);
        element.addEventListener('click', handleSpeechClick);
    });
}

async function handleSpeechClick(e) {
    // Only allow TTS when Marathi is selected
    if (currentLanguage !== 'mr') {
        return;
    }
    
    const text = e.target.textContent.trim();
    
    if (e.target.tagName === 'BUTTON' && e.target.type === 'submit') {
        return;
    }
    
    if (e.target.classList.contains('lang-btn')) {
        return;
    }
    
    if (e.target.classList.contains('btn') && e.target.tagName === 'A' && e.target.href) {
        e.preventDefault();
        e.stopPropagation();
        await speakText(text);
        const href = e.target.href;
        setTimeout(() => {
            window.location.href = href;
        }, 1000);
        return;
    }
    
    if (e.target.tagName === 'A' && e.target.href) {
        return;
    }
    
    await speakText(text);
}

async function setLanguage(lang) {
    currentLanguage = lang;
    document.getElementById('html').lang = lang;
    
    // Show loading indicator
    showTranslationLoading();
    
    try {
        if (lang === 'mr' && translationEnabled) {
            // Use dynamic translation for Marathi
            await translatePageToMarathi();
        } else {
            // Use static translations (fallback)
            const elements = document.querySelectorAll('[data-' + lang + ']');
            elements.forEach(element => {
                element.textContent = element.getAttribute('data-' + lang);
            });
        }
    } catch (error) {
        console.error('Translation failed:', error);
        // Fallback to static translations
        const elements = document.querySelectorAll('[data-' + lang + ']');
        elements.forEach(element => {
            element.textContent = element.getAttribute('data-' + lang);
        });
    }
    
    // Update language buttons
    const langButtons = document.querySelectorAll('.lang-btn');
    langButtons.forEach(btn => {
        btn.classList.remove('active');
        if (btn.getAttribute('data-lang') === lang) {
            btn.classList.add('active');
        }
    });
    
    // Stop any current speech when switching languages
    if (currentSpeech) {
        stopSpeech();
    }
    
    // Update speech controls visibility
    updateSpeechControls();
    
    // Hide loading indicator
    hideTranslationLoading();
}

async function translatePageToMarathi() {
    const container = document.body;
    
    // First, try to use static translations where available
    const staticElements = document.querySelectorAll('[data-mr]');
    staticElements.forEach(element => {
        element.textContent = element.getAttribute('data-mr');
    });
    
    // Then translate dynamic content
    if (translationEnabled) {
        await window.TranslationService.translateDynamicContent(container);
    }
}

function showTranslationLoading() {
    // Create or show loading indicator
    let loader = document.getElementById('translation-loader');
    if (!loader) {
        loader = document.createElement('div');
        loader.id = 'translation-loader';
        loader.innerHTML = `
            <div style="position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); 
                        background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); 
                        z-index: 10000; display: flex; align-items: center; gap: 10px;">
                <div class="spinner" style="width: 20px; height: 20px; border: 2px solid #f3f3f3; 
                                          border-top: 2px solid #2f855a; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                <span>Translating...</span>
            </div>
            <style>
                @keyframes spin {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
            </style>
        `;
        document.body.appendChild(loader);
    }
    loader.style.display = 'block';
}

function hideTranslationLoading() {
    const loader = document.getElementById('translation-loader');
    if (loader) {
        loader.style.display = 'none';
    }
}

// Enhanced function to handle dynamic content translation
async function translateDynamicContent() {
    if (!translationEnabled || currentLanguage !== 'mr') {
        return;
    }
    
    // Find elements that don't have static translations
    const dynamicElements = document.querySelectorAll('*:not([data-mr])');
    
    for (const element of dynamicElements) {
        if (element.childNodes.length === 1 && element.childNodes[0].nodeType === Node.TEXT_NODE) {
            const text = element.textContent.trim();
            if (text && text.length > 0) {
                try {
                    const translatedText = await window.TranslationService.translateText(text, 'en', 'mr');
                    element.setAttribute('data-original-text', text);
                    element.textContent = translatedText;
                } catch (error) {
                    console.warn('Failed to translate:', text, error);
                }
            }
        }
    }
}

// Function to revert all translations
function revertAllTranslations() {
    if (translationEnabled) {
        window.TranslationService.revertTranslations(document.body);
    }
    
    // Also revert static translations
    const elements = document.querySelectorAll('[data-en]');
    elements.forEach(element => {
        element.textContent = element.getAttribute('data-en');
    });
}

async function initEnhancedSpeech() {
    // Initialize translation service first
    await initTranslationService();
    
    // Then initialize speech synthesis
    initSpeechSynthesis();
    addSpeechListeners();
    
    // Add event listeners for speech controls
    document.getElementById('stop-speech').addEventListener('click', stopSpeech);
    document.getElementById('pause-speech').addEventListener('click', pauseSpeech);
    document.getElementById('resume-speech').addEventListener('click', resumeSpeech);
    
    updateSpeechControls();
    
    // Set up observer for dynamic content
    setupDynamicContentObserver();
}

function setupDynamicContentObserver() {
    // Observe DOM changes for dynamic content
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'childList' && currentLanguage === 'mr' && translationEnabled) {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        // Translate new content
                        window.TranslationService.translateDynamicContent(node);
                    }
                });
            }
        });
    });
    
    observer.observe(document.body, {
        childList: true,
        subtree: true
    });
}

// Export functions for global access
window.FarmChainEnhancedSpeech = {
    speakText,
    stopSpeech,
    pauseSpeech,
    resumeSpeech,
    setLanguage,
    initEnhancedSpeech,
    translateDynamicContent,
    revertAllTranslations,
    getTranslationStats: () => window.TranslationService.getStats()
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', initEnhancedSpeech);

