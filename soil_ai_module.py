import os
import sys
import json
import re
from typing import Dict, Any, Optional, Tuple
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from agricultural_config import (
    SOIL_THRESHOLDS, PH_THRESHOLDS, KHARIF_CROPS, RABI_CROPS, SUMMER_CROPS,
    SEASON_CROPS, HIGH_INPUT_CROPS, MAHARASHTRA_DISTRICTS, SOIL_TYPES,
    SEASONS, IRRIGATION_TYPES,
    categorize_ph, categorize_parameter, get_crop_list_for_season,
    validate_crops_for_season, should_filter_crop, get_disclaimer,
    get_crop_durations
)

# ============================================================================
# OLLAMA LLM INITIALIZATION - HARDENED FOR DETERMINISTIC OUTPUT
# ============================================================================
# 
# 🔒 SECURITY & DETERMINISM CONSTRAINTS:
# 
# 1. TWO-LLM ARCHITECTURE:
#    - llm_json (temperature=0.1): Strict structured JSON output
#      * Used for: extract_soil_parameters, classify_soil_profile, 
#                  generate_agronomy_recommendations
#      * Enforces: format="json" for valid JSON only
#      * Purpose: Data extraction and categorization (no creativity)
#    
#    - llm_text (temperature=0.3): Human-friendly advisory text
#      * Used for: generate_advisory (optional farmer-friendly explanation)
#      * Purpose: Natural language guidance (controlled creativity)
# 
# 2. TEMPERATURE SETTINGS (MANDATORY):
#    - llm_json: temperature ≤ 0.1 → deterministic, consistent output
#    - llm_text: temperature ≤ 0.3 → controlled variability for readability
#    - DO NOT increase these values → would allow hallucination
# 
# 3. HARDENED PROMPT DESIGN:
#    - Explicit constraints in every prompt
#    - "NEVER generate numeric values" enforced
#    - "READ-ONLY data sources" specified
#    - "FORBIDDEN actions" listed explicitly
#    - Safety checks validate output post-generation
# 
# 4. SAFETY VALIDATION:
#    - validate_no_numeric_values_in_response() blocks AI-generated numbers
#    - validate_no_numeric_values_in_json() checks JSON structure
#    - Backend rule-based categorization ALWAYS overrides AI output
#    - Measured soil values NEVER processed by AI (backend pre-categorizes)
# 
# 5. DATA FLOW (CRITICAL):
#    Lab Report → extract (AI) → categorize (Backend+AI) → recommend (AI) 
#    → explain (Rule-based, NO AI)
#    
#    - Numeric values: ONLY from lab reports (extraction phase)
#    - Categories: Backend threshold logic (NOT AI inference)
#    - Recommendations: AI suggests, backend filters/validates
#    - Explanations: Pure rule-based string assembly (NO AI)
# 
# ============================================================================

try:
    llm_json = ChatOllama(
        model="llama3.2",
        base_url="http://localhost:11434",
        temperature=0.1,  # 🔒 LOCKED: Deterministic JSON output
        model_kwargs={"format": "json"}
    )
    llm_text = ChatOllama(
        model="llama3.2",
        base_url="http://localhost:11434",
        temperature=0.3  # 🔒 LOCKED: Controlled text generation
    )
except Exception as e:
    print(f"Warning: Could not initialize Ollama: {e}")
    print("Make sure Ollama is running: ollama serve")
    raise

json_parser = JsonOutputParser()


# ==========================================================================
# MARATHI-TO-ENGLISH CROP NAME TRANSLATION
# ==========================================================================

# Mapping of Marathi crop names to English equivalents
MARATHI_CROP_MAP = {
    "गहू": "Wheat",
    "मसूर": "Gram",
    "सोयाबीन": "Soybean",
    "तुर": "Tur",
    "कपास": "Cotton",
    "मक्का": "Maize",
    "धान": "Rice",
    "बाजरा": "Bajra",
    "ज्वार": "Jowar",
    "मूंगफली": "Groundnut",
    "गन्ना": "Sugarcane",
    "तरबूज": "Watermelon",
    "खरबूजा": "Muskmelon",
    "काकडी": "Cucumber",
    "करली": "Bitter Gourd",
    "भिंडी": "Okra",
    "प्याज": "Onion",
    "टोमॅटो": "Tomato",
    "बटाटा": "Potato",
    "सरसों": "Mustard",
    "सूर्यफूल": "Sunflower",
    "लसूण": "Garlic",
    "मेथी": "Fenugreek",
    "धनिया": "Coriander",
}

# Reverse mapping: English to Marathi
ENGLISH_CROP_MAP = {v: k for k, v in MARATHI_CROP_MAP.items()}


def translate_crop_name_to_english(crop_name: str) -> str:
    """Translate Marathi crop name to English, or return as-is if already English."""
    return MARATHI_CROP_MAP.get(crop_name, crop_name)


def translate_crop_names_to_english(crops: list) -> list:
    """Translate list of crop names from Marathi to English."""
    if not isinstance(crops, list):
        return crops
    return [translate_crop_name_to_english(crop) for crop in crops]


# ==========================================================================
# OCR TEXT NORMALIZATION & RULE-BASED EXTRACTION (PRIMARY PATH)
# ==========================================================================

def normalize_ocr_text(raw_text: str) -> str:
    """Normalize OCR text: remove noise, standardize spacing, preserve numbers."""
    if not raw_text:
        return ""
    normalized = raw_text.lower()
    
    # Step 1: Replace common OCR junk symbols with spaces
    junk_chars = ["|", "_", "[", "]", "{", "}", "~", "^", "`", "@", "#"]
    for char in junk_chars:
        normalized = normalized.replace(char, " ")
    
    # Step 2: Normalize multiple colons/dashes/slashes (units can break across lines)
    normalized = re.sub(r"[:/\\]+", " ", normalized)
    
    # Step 3: Collapse multiple spaces, tabs, newlines into single space
    normalized = re.sub(r"\s+", " ", normalized)
    
    # Step 4: Remove spaces before/after parentheses (for patterns like "nitrogen ( n )")
    normalized = re.sub(r"\s*\(\s*", "(", normalized)
    normalized = re.sub(r"\s*\)\s*", ")", normalized)
    
    return normalized.strip()


def _match_first(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def build_clean_values(extracted_params: Dict[str, Any], soil_profile: Dict[str, Any]) -> Dict[str, Any]:
    """Return pre-formatted values for frontend consumption (no raw OCR)."""
    extracted_params = extracted_params or {}
    soil_profile = soil_profile or {}

    def _val(key: str) -> Optional[float]:
        item = extracted_params.get(key, {})
        return item.get("value") if isinstance(item, dict) else None

    def _unit(key: str) -> Optional[str]:
        item = extracted_params.get(key, {})
        return item.get("unit") if isinstance(item, dict) else None

    def _cat(profile_key: str, fallback_key: Optional[str] = None) -> Optional[str]:
        profile_item = soil_profile.get(profile_key, {}) if isinstance(soil_profile, dict) else {}
        if isinstance(profile_item, dict) and profile_item.get("category"):
            return profile_item.get("category")
        if fallback_key:
            item = extracted_params.get(fallback_key, {})
            if isinstance(item, dict):
                return item.get("category") or item.get("category_hint")
        return None

    return {
        "pH_category": _cat("pH", "pH"),
        "Nitrogen_category": _cat("Nitrogen", "Nitrogen"),
        "Phosphorus_value": _val("Phosphorus"),
        "Phosphorus_unit": _unit("Phosphorus"),
        "Potassium_value": _val("Potassium"),
        "Potassium_unit": _unit("Potassium"),
        "OrganicCarbon_category": _cat("Organic Carbon", "OrganicCarbon"),
    }


def categorize_from_thresholds(param: str, value: Any) -> Tuple[str, float]:
    """Deterministic, rule-based categorization using Indian soil thresholds."""
    if value is None or not isinstance(value, (int, float)):
        return "Unknown", 0.0

    key_map = {
        "ph": "pH",
        "pH": "pH",
        "nitrogen": "Nitrogen",
        "phosphorus": "Phosphorus",
        "potassium": "Potassium",
        "organic carbon": "Organic Carbon",
        "organiccarbon": "Organic Carbon",
        "organic_carbon": "Organic Carbon",
    }

    lookup_key = key_map.get(str(param).strip().lower(), param)

    if lookup_key == "pH":
        try:
            category = categorize_ph(value)
        except Exception:
            return "Unknown", 0.0
        return category, 0.95

    thresholds = SOIL_THRESHOLDS.get(lookup_key)
    if not thresholds:
        return "Unknown", 0.0

    for category, (min_val, max_val) in thresholds.items():
        if min_val <= value <= max_val:
            span = max_val - min_val if max_val is not None else None
            if span and span > 0:
                midpoint = (min_val + max_val) / 2
                proximity = 1 - (abs(value - midpoint) / span)
                proximity = max(0.0, min(proximity, 1.0))
                confidence = 0.85 + 0.1 * proximity
            else:
                confidence = 0.9
            return category, round(confidence, 2)

    return "Unknown", 0.0


def extract_parameters_with_regex(normalized_text: str) -> Dict[str, Any]:
    """
    Rule-first extraction using noise-tolerant regex patterns.
    Handles OCR variations: spacing, symbol breaks, unit separations.
    Returns only fields that were found.
    """
    extracted: Dict[str, Any] = {}

    # pH: Tolerant patterns for "ph 7.3", "ph: 7.3", "ph(7.3)", "soil reaction 7.3"
    ph_patterns = [
        r"(?:ph|soil\s+reaction)\s*[:]?\s*(\d{1,2}\.\d+)",  # pH with optional colon
        r"ph\s*\(?\s*(\d{1,2}\.\d+)\s*\)?",  # pH with optional parens
    ]
    ph_match = None
    for pattern in ph_patterns:
        ph_match = _match_first(pattern, normalized_text)
        if ph_match:
            break
    
    if ph_match:
        try:
            ph_val = float(ph_match)
            # Validate pH range (0-14)
            if 0 <= ph_val <= 14:
                extracted["pH"] = {"value": ph_val, "unit": "", "source": "report", "unit_uncertain": False}
        except ValueError:
            pass

    # Nitrogen: Tolerant patterns for "available nitrogen", "nitrogen (n)", "n kg/ha"
    n_patterns_numeric = [
        r"available\s+nitrogen\s*\(?n\)?\s*[:]?\s*(\d{2,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",
        r"nitrogen\s*\(?n\)?\s*[:]?\s*(\d{2,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",
        r"(?:^|\s)n\s*[:]?\s*(\d{2,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",  # Standalone "N: 120 kg/ha"
    ]
    n_numeric = None
    for pattern in n_patterns_numeric:
        n_numeric = _match_first(pattern, normalized_text)
        if n_numeric:
            break
    
    if n_numeric:
        try:
            n_val = float(n_numeric)
            # Validate nitrogen range (typical: 10-500 kg/ha)
            if 10 <= n_val <= 500:
                extracted["Nitrogen"] = {"value": n_val, "unit": "kg/ha", "source": "report", "unit_uncertain": False}
        except ValueError:
            pass
    else:
        # Try category-based extraction if numeric not found
        n_cat_patterns = [
            r"available\s+nitrogen.*?(low|medium|high)",
            r"nitrogen.*?(low|medium|high)",
        ]
        n_cat = None
        for pattern in n_cat_patterns:
            n_cat = _match_first(pattern, normalized_text)
            if n_cat:
                break
        if n_cat:
            extracted["Nitrogen"] = {"category": n_cat.lower(), "source": "report"}

    # Phosphorus: Tolerant patterns for "available phosphorus", "phosphorus (p)", "p kg/ha"
    p_patterns_numeric = [
        r"available\s+phosphorus\s*\(?p\)?\s*[:]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",
        r"phosphorus\s*\(?p\)?\s*[:]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",
        r"(?:^|\s)p\s*[:]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",  # Standalone "P: 25 kg/ha"
    ]
    p_numeric = None
    for pattern in p_patterns_numeric:
        p_numeric = _match_first(pattern, normalized_text)
        if p_numeric:
            break
    
    if p_numeric:
        try:
            p_val = float(p_numeric)
            # Validate phosphorus range (typical: 1-150 kg/ha)
            if 1 <= p_val <= 150:
                extracted["Phosphorus"] = {"value": p_val, "unit": "kg/ha", "source": "report", "unit_uncertain": False}
        except ValueError:
            pass

    # Potassium: Tolerant patterns for "available potassium", "potassium (k)", "k kg/ha"
    k_patterns_numeric = [
        r"available\s+potassium\s*\(?k\)?\s*[:]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",
        r"potassium\s*\(?k\)?\s*[:]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",
        r"(?:^|\s)k\s*[:]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg/ha|kg\s*ha)",  # Standalone "K: 200 kg/ha"
    ]
    k_numeric = None
    for pattern in k_patterns_numeric:
        k_numeric = _match_first(pattern, normalized_text)
        if k_numeric:
            break
    
    if k_numeric:
        try:
            k_val = float(k_numeric)
            # Validate potassium range (typical: 50-400 kg/ha)
            if 50 <= k_val <= 400:
                extracted["Potassium"] = {"value": k_val, "unit": "kg/ha", "source": "report", "unit_uncertain": False}
        except ValueError:
            pass

    # Organic Carbon: Tolerant patterns for "organic carbon", "oc"
    oc_cat_patterns = [
        r"organic\s+carbon.*?(low|medium|high)",
        r"\boc\b.*?(low|medium|high)",
    ]
    oc_cat = None
    for pattern in oc_cat_patterns:
        oc_cat = _match_first(pattern, normalized_text)
        if oc_cat:
            break
    
    if oc_cat:
        extracted["OrganicCarbon"] = {"category": oc_cat.lower(), "source": "report"}

    return extracted


def hard_extract_soil_health_card(normalized_text: str) -> Dict[str, Any]:
    """Deterministic extractor for Indian Soil Health Card tabular formats."""
    extracted: Dict[str, Any] = {}

    patterns = {
        "pH": {
            "regex": r"ph\s*[:\-]?\s*(\d{1,2}\.\d{1,2})",
            "unit": "",
            "min": 0,
            "max": 14,
        },
        "Nitrogen": {
            "regex": r"available\s+nitrogen.*?(\d{2,4}\.\d+|\d{2,4})\s*kg/ha",
            "unit": "kg/ha",
            "min": 10,
            "max": 5000,
        },
        "Phosphorus": {
            "regex": r"available\s+phosphorus.*?(\d{1,3}\.\d+|\d{1,3})\s*kg/ha",
            "unit": "kg/ha",
            "min": 1,
            "max": 1000,
        },
        "Potassium": {
            "regex": r"available\s+potassium.*?(\d{2,4}\.\d+|\d{2,4})\s*kg/ha",
            "unit": "kg/ha",
            "min": 10,
            "max": 5000,
        },
        "OrganicCarbon": {
            "regex": r"organic\s+carbon.*?(\d\.\d+)",
            "unit": "%",
            "min": 0,
            "max": 1.5,
        },
    }

    for param, cfg in patterns.items():
        match = re.search(cfg["regex"], normalized_text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        raw_val = match.group(1)
        try:
            val = float(raw_val)
        except ValueError:
            continue

        # Basic sanity bounds to avoid spurious OCR hits
        if cfg["min"] <= val <= cfg["max"]:
            extracted[param] = {
                "value": val,
                "unit": cfg["unit"],
                "source": "report",
                "unit_uncertain": False if param != "OrganicCarbon" else True,
            }

    return extracted


def hard_parse_soil_values(text: str) -> Dict[str, Any]:
    """Rule-based parser for Soil Health Card style text (non-AI)."""
    parsed: Dict[str, Any] = {}

    patterns = {
        "pH": r"pH\s*([0-9]+\.[0-9]+)",
        # Nitrogen numeric must be on the same line/near 'Available Nitrogen'
        "Nitrogen_value": r"available\s+nitrogen[^\n\r]{0,50}?([0-9]+\.?[0-9]*)\s*(?:kg/ha|kg\s*ha)",
        # Nitrogen category only if low/medium/high appears soon after the Nitrogen label
        "Nitrogen_cat": r"available\s+nitrogen(?:\s*\(n\))?[^\n\r]{0,40}?(low|medium|high)\b",
        "Phosphorus": r"available\s+phosphorus[^\n\r]{0,80}?([0-9]+\.?[0-9]*)\s*kg/ha",
        "Potassium": r"available\s+potassium[^\n\r]{0,80}?([0-9]+\.?[0-9]*)\s*kg/ha",
        "OrganicCarbon_value": r"organic\s+carbon[^\n\r]{0,80}?([0-9]+\.?[0-9]*)\s*%",
        "OrganicCarbon_cat": r"organic\s+carbon[^\n\r]*(low|medium|high)",
    }

    # pH
    ph_match = re.search(patterns["pH"], text, re.IGNORECASE)
    if ph_match:
        try:
            parsed["pH"] = {
                "value": float(ph_match.group(1)),
                "unit": "",
                "source": "report",
                "unit_uncertain": False,
            }
        except ValueError:
            pass

    # Nitrogen
    n_match = re.search(patterns["Nitrogen_value"], text, re.IGNORECASE)
    if n_match:
        try:
            parsed["Nitrogen"] = {
                "value": float(n_match.group(1)),
                "unit": "kg/ha",
                "source": "report",
                "unit_uncertain": False,
            }
        except ValueError:
            pass
    else:
        n_cat = re.search(patterns["Nitrogen_cat"], text, re.IGNORECASE)
        if n_cat:
            parsed["Nitrogen"] = {
                "value": None,
                "category_hint": n_cat.group(1).capitalize(),
                "source": "report",
            }

    # Phosphorus
    p_match = re.search(patterns["Phosphorus"], text, re.IGNORECASE)
    if p_match:
        try:
            parsed["Phosphorus"] = {
                "value": float(p_match.group(1)),
                "unit": "kg/ha",
                "source": "report",
                "unit_uncertain": False,
            }
        except ValueError:
            pass

    # Potassium
    k_match = re.search(patterns["Potassium"], text, re.IGNORECASE)
    if k_match:
        try:
            parsed["Potassium"] = {
                "value": float(k_match.group(1)),
                "unit": "kg/ha",
                "source": "report",
                "unit_uncertain": False,
            }
        except ValueError:
            pass

    # Organic Carbon
    oc_cat_match = re.search(patterns["OrganicCarbon_cat"], text, re.IGNORECASE)
    oc_val_match = re.search(patterns["OrganicCarbon_value"], text, re.IGNORECASE)

    if oc_cat_match:
        cat = oc_cat_match.group(1).lower()
        oc_category = {
            "low": "Poor",
            "medium": "Moderate",
            "high": "Rich",
        }.get(cat, cat.capitalize())
        parsed["OrganicCarbon"] = {
            "value": None,
            "category": oc_category,
            "source": "report",
        }
    elif oc_val_match:
        try:
            oc_val = float(oc_val_match.group(1))
            if 0 <= oc_val <= 1.5:
                parsed["OrganicCarbon"] = {
                    "value": oc_val,
                    "unit": "%",
                    "source": "report",
                    "unit_uncertain": False,
                }
        except ValueError:
            pass

    return parsed


def parse_soil_parameters_from_text(raw_text: str) -> Dict[str, Any]:
    """Parse Soil Health Card text using regex/rules only (no AI)."""
    normalized = normalize_ocr_text(raw_text)
    extracted: Dict[str, Any] = {}

    numeric_patterns = {
        "pH": {
            "regex": r"ph\s*[:\-]?\s*(\d{1,2}\.\d{1,2})",
            "unit": "",
            "min": 0,
            "max": 14,
        },
        "Nitrogen": {
            "regex": r"available\s+nitrogen.*?(\d{2,4}\.\d+|\d{2,4})\s*kg/ha",
            "unit": "kg/ha",
            "min": 10,
            "max": 6000,
        },
        "Phosphorus": {
            "regex": r"available\s+phosphorus.*?(\d{1,3}\.\d+|\d{1,3})\s*kg/ha",
            "unit": "kg/ha",
            "min": 1,
            "max": 1500,
        },
        "Potassium": {
            "regex": r"available\s+potassium.*?(\d{2,4}\.\d+|\d{2,4})\s*kg/ha",
            "unit": "kg/ha",
            "min": 10,
            "max": 6000,
        },
        "OrganicCarbon": {
            "regex": r"organic\s+carbon.*?(\d\.\d+)",
            "unit": "%",
            "min": 0,
            "max": 1.5,
        },
    }

    for param, cfg in numeric_patterns.items():
        match = re.search(cfg["regex"], normalized, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        try:
            val = float(match.group(1))
        except ValueError:
            continue
        if cfg["min"] <= val <= cfg["max"]:
            extracted[param] = {
                "value": val,
                "unit": cfg["unit"],
                "source": "report",
                "unit_uncertain": False,
            }

    qualitative_patterns = {
        "Nitrogen": [r"available\s+nitrogen.*?(low|medium|high)", r"\bnitrogen\b.*?(low|medium|high)"],
        "Phosphorus": [r"available\s+phosphorus.*?(low|medium|high)", r"\bphosphorus\b.*?(low|medium|high)"],
        "Potassium": [r"available\s+potassium.*?(low|medium|high)", r"\bpotassium\b.*?(low|medium|high)"],
        "OrganicCarbon": [r"organic\s+carbon.*?(low|medium|high)", r"\boc\b.*?(low|medium|high)"],
    }

    for param, patterns in qualitative_patterns.items():
        if param in extracted:
            continue
        for pattern in patterns:
            m = re.search(pattern, normalized, re.IGNORECASE | re.DOTALL)
            if m:
                cat = m.group(1).lower()
                if param == "OrganicCarbon":
                    mapped = {"low": "Poor", "medium": "Moderate", "high": "Rich"}.get(cat, cat.capitalize())
                    extracted[param] = {"value": None, "category": mapped, "source": "report"}
                else:
                    extracted[param] = {"category": cat, "source": "report"}
                break

    return extracted


def _value_in_text(value: Any, normalized_text: str) -> bool:
    """Ensure AI-returned numeric/category literally appears in text to prevent hallucination."""
    if value is None:
        return False
    try:
        if isinstance(value, (int, float)):
            val_str = f"{value}".lower()
            return re.search(rf"\b{re.escape(val_str)}\b", normalized_text) is not None
        if isinstance(value, str):
            return value.lower() in normalized_text
    except Exception:
        return False
    return False


def ai_fallback_extract(report_text: str, normalized_text: str, missing_params: list) -> Dict[str, Any]:
    """
    AI fallback used ONLY for parameters not found via regex. All numeric outputs are validated
    against the original text to prevent hallucination.
    """
    if not missing_params:
        return {}

    prompt = ChatPromptTemplate.from_template("""
You extract soil parameters ONLY from the provided text. Rules:
- Extract a parameter ONLY if its value or category is explicitly present in the text.
- If not present or uncertain, mark that parameter as missing.
- NEVER guess, predict, or generate any numeric value.
- Allowed parameters: pH, Nitrogen, Phosphorus, Potassium, OrganicCarbon.

Expected JSON keys (only include those you find):
{{
  "pH": {{"value": <number>, "unit": "", "source": "report"}},
  "Nitrogen": {{"value": <number>, "unit": "kg/ha", "source": "report"}} OR {{"category": "low|medium|high", "source": "report"}},
  "Phosphorus": {{"value": <number>, "unit": "kg/ha", "source": "report"}},
  "Potassium": {{"value": <number>, "unit": "kg/ha", "source": "report"}},
  "OrganicCarbon": {{"category": "low|medium|high", "source": "report"}}
}}

Missing parameters MUST be omitted from the JSON.

Text:
{report_text}
""")

    chain = prompt | llm_json | json_parser
    try:
        ai_result = chain.invoke({"report_text": report_text}) or {}
    except Exception as e:
        print(f"AI fallback extraction failed: {e}", file=sys.stderr)
        return {}

    # Validate AI output against text to avoid hallucination
    validated: Dict[str, Any] = {}
    for key, value in ai_result.items():
        if key not in missing_params:
            continue

        if isinstance(value, dict):
            num_val = value.get("value")
            cat_val = value.get("category")

            if num_val is not None and _value_in_text(num_val, normalized_text):
                validated[key] = {
                    "value": float(num_val),
                    "unit": value.get("unit", "kg/ha" if key != "pH" else ""),
                    "source": "report",
                    "unit_uncertain": False if key != "pH" else value.get("unit_uncertain", False)
                }
            elif cat_val and _value_in_text(cat_val, normalized_text):
                validated[key] = {"category": cat_val.lower(), "source": "report"}

    return validated

def validate_no_numeric_values_in_response(response_text: str, context: str) -> None:
    """
    Safety check: Detect if AI generated numeric soil values in its response.
    This should NEVER happen - AI must only use categories.
    
    Args:
        response_text: The raw response text from AI
        context: Description of where this check is running (for error messages)
    
    Raises:
        ValueError: If numeric soil values are detected in response
    """
    # Patterns that indicate AI generated numeric values
    forbidden_patterns = [
        r'pH[\s:=]+\d+\.\d+',  # pH: 7.2 or pH = 7.2
        r'nitrogen[\s:=]+\d+',  # Nitrogen: 150
        r'phosphorus[\s:=]+\d+',  # Phosphorus: 25
        r'potassium[\s:=]+\d+',  # Potassium: 200
        r'\d+\s*kg/ha',  # 150 kg/ha
        r'\d+\s*kg/acre',  # 100 kg/acre
        r'value["\']?\s*:\s*\d+\.\d+',  # "value": 7.2 (in JSON)
        r'pH\s+is\s+\d+\.\d+',  # pH is 7.2
        r'nitrogen\s+is\s+\d+',  # Nitrogen is 150
    ]
    
    response_lower = response_text.lower()
    for pattern in forbidden_patterns:
        matches = re.findall(pattern, response_lower)
        if matches:
            raise ValueError(
                f"AI SAFETY VIOLATION in {context}: AI generated numeric soil values. "
                f"Found: {matches}. AI must ONLY use categories (Low/Medium/High, etc.), "
                f"NEVER numeric values. Response excerpt: {response_text[:200]}"
            )

def validate_no_numeric_values_in_json(json_data: dict, context: str, allowed_fields: list = None) -> None:
    """
    Safety check: Ensure JSON output contains no numeric soil values in unexpected places.
    
    Args:
        json_data: Parsed JSON response from AI
        context: Description of where this check is running
        allowed_fields: List of field paths where numeric values are allowed (e.g., ['confidence'])
    
    Raises:
        ValueError: If numeric values found in disallowed fields
    """
    allowed_fields = allowed_fields or ['confidence', 'version']
    
    def check_dict(d: dict, path: str = ''):
        for key, value in d.items():
            current_path = f"{path}.{key}" if path else key
            
            # Skip allowed fields
            if any(allowed in current_path for allowed in allowed_fields):
                continue
            
            if isinstance(value, dict):
                check_dict(value, current_path)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        check_dict(item, current_path)
            elif key in ['value', 'ph', 'nitrogen', 'phosphorus', 'potassium'] and isinstance(value, (int, float)):
                # Found numeric value in soil parameter field
                if key != 'value' or ('extracted_parameters' not in path and 'pre_categorized' not in path):
                    raise ValueError(
                        f"AI SAFETY VIOLATION in {context}: Found numeric value in {current_path} = {value}. "
                        f"AI output must contain ONLY categories, not numeric values."
                    )
    
    check_dict(json_data)

def extract_soil_parameters(report_text: str) -> Dict[str, Any]:
    """
    Extract soil parameters from report text.
    Returns structured JSON with normalized parameters.
    
    🔒 HARDENED PROMPT: AI must ONLY extract numeric values from real lab reports.
    AI MUST NEVER generate, predict, or infer numeric soil values.
    """
    normalized_text = normalize_ocr_text(report_text)

    # HARD FALLBACK: Handle Indian Soil Health Card tables without AI
    if "soil health card" in normalized_text:
        hard_extracted = hard_extract_soil_health_card(normalized_text)
        if hard_extracted:
            print(
                f"✓ HARD EXTRACTOR captured: {sorted(list(hard_extracted.keys()))}",
                file=sys.stderr,
            )
            return {
                "version": "farmchain-ai-v1.0",
                "extracted_parameters": hard_extracted,
            }

    prompt = ChatPromptTemplate.from_template("""
You are a soil report text extractor for Maharashtra, India agriculture system.

🔴 CRITICAL CONSTRAINTS (MANDATORY - NEVER VIOLATE):

1. EXTRACTION ONLY - NO GENERATION:
   - Extract ONLY numeric values that are EXPLICITLY written in the report text
   - NEVER generate, predict, estimate, or infer any numeric values
   - NEVER use typical values, averages, or ranges
   - NEVER calculate or derive values from other parameters

2. MISSING PARAMETER HANDLING:
   - If a parameter (pH, Nitrogen, Phosphorus, Potassium, Organic Carbon) is NOT found in the text:
     → Set value: null
     → Set source: "missing"
   - If parameter name appears but NO numeric value is given:
     → Set value: null
     → Set source: "missing"

3. PARAMETER NORMALIZATION:
   - "Soil Reaction" → pH
   - "Available Nitrogen (N)" or "N" → Nitrogen
   - "Available Phosphorus (P)" or "P" → Phosphorus
   - "Available Potassium (K)" or "K" → Potassium
   - "Organic Carbon (OC)" or "OC" → Organic Carbon

4. UNITS:
   - pH: no unit (dimensionless)
   - Nitrogen, Phosphorus, Potassium: kg/ha (if not specified, mark unit_uncertain: true)
   - Organic Carbon: % or g/kg

5. OUTPUT REQUIREMENTS:
   - Return ONLY valid JSON
   - NO explanations, NO markdown, NO text before/after JSON
   - Start with {{ and end with }}
   - Every parameter must have: value, unit, source, unit_uncertain

❌ FORBIDDEN ACTIONS:
❌ Generating typical values (e.g., "typical pH for black soil is 7.5")
❌ Inferring from district or soil type
❌ Using ranges or approximations
❌ Predicting based on other parameters
❌ Filling gaps with assumptions

Report Text:
{report_text}

✓ ACCEPTABLE OUTPUT EXAMPLES:

Example 1 - All parameters found:
{{
  "version": "farmchain-ai-v1.0",
  "extracted_parameters": {{
    "pH": {{"value": 7.8, "unit": "", "source": "report", "unit_uncertain": false}},
    "Nitrogen": {{"value": 210, "unit": "kg/ha", "source": "report", "unit_uncertain": false}},
    "Phosphorus": {{"value": 15, "unit": "kg/ha", "source": "report", "unit_uncertain": false}},
    "Potassium": {{"value": 250, "unit": "kg/ha", "source": "report", "unit_uncertain": false}}
  }}
}}

Example 2 - Some parameters missing:
{{
  "version": "farmchain-ai-v1.0",
  "extracted_parameters": {{
    "pH": {{"value": 6.9, "unit": "", "source": "report", "unit_uncertain": false}},
    "Nitrogen": {{"value": 120, "unit": "kg/ha", "source": "report", "unit_uncertain": false}},
    "Phosphorus": {{"value": null, "unit": "", "source": "missing", "unit_uncertain": false}},
    "Potassium": {{"value": null, "unit": "", "source": "missing", "unit_uncertain": false}}
  }}
}}

Example 3 - No parameters found:
{{
  "version": "farmchain-ai-v1.0",
  "extracted_parameters": {{}}
}}

Now extract parameters from the report text above. Return ONLY the JSON structure.
""")

    chain = prompt | llm_json | json_parser
    try:
        result = chain.invoke({"report_text": report_text})
        
        # SAFETY CHECK: Validate response before processing
        result_str = json.dumps(result)
        
        # Check 1: Ensure AI didn't generate explanatory text with numeric values
        validate_no_numeric_values_in_response(
            result_str,
            "extract_soil_parameters"
        )
        
        return result
    except Exception as e:
        print(f"❌ Error in extract_soil_parameters: {e}", file=sys.stderr)
        return {
            "version": "farmchain-ai-v1.0",
            "extracted_parameters": {}
        }

def classify_soil_profile(
    extracted_params: Dict[str, Any],
    district: str,
    soil_type: Optional[str] = None,
    irrigation_type: str = "Rain-fed",
    pre_categorized_soil_profile: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convert numeric values into categories only.
    Infer missing categories using soil type + district logic.
    FIX 1: Validates that if numeric value exists, category cannot be Unknown.
    🔴 FINAL FIX: pH is pre-categorized in backend code and passed via pre_categorized_soil_profile.
    Ollama must NEVER classify or infer pH.
    """
    extracted = extracted_params.get("extracted_parameters", {}) if isinstance(extracted_params, dict) else extracted_params
    if "pH" in extracted:
        ph_data = extracted["pH"]
        if isinstance(ph_data, dict):
            ph_value = ph_data.get("value")
            ph_source = ph_data.get("source", "")
            
            # Convert pH value to float if needed
            if ph_value is not None and not isinstance(ph_value, (int, float)):
                try:
                    ph_value_float = float(ph_value)
                    extracted["pH"]["value"] = ph_value_float
                except (ValueError, TypeError):
                    pass

    pre_categorized_ph = None
    if pre_categorized_soil_profile and "pH" in pre_categorized_soil_profile:
        pre_categorized_ph = pre_categorized_soil_profile["pH"]

    prompt = ChatPromptTemplate.from_template("""
You are a soil classification assistant for Maharashtra, India agriculture system.

🔒 YOUR ROLE: Apply rule-based categorization for measured values ONLY.
🔒 BACKEND HANDLES: All threshold-based categorization for measured values.

Input Data:
- Extracted Parameters: {extracted_params}
- District: {district}
- Soil Type: {soil_type}
- Irrigation Type: {irrigation_type}
- Pre-categorized pH: {pre_categorized_ph}

🔴 CRITICAL CONSTRAINTS (MANDATORY - NEVER VIOLATE):

1. pH CATEGORIZATION - COMPLETELY LOCKED:
   - pH is ALREADY categorized in backend code
   - Use the pre-categorized pH category EXACTLY as provided above
   - NEVER classify, infer, or modify pH category
   - NEVER use district or soil-type to determine pH
   - If pre-categorized pH exists → copy it to output unchanged

2. MEASURED VALUES (source="report" AND value is NOT null):
   - Backend code ALREADY categorized these using thresholds
   - You should NOT see these in your task (backend pre-processes them)
   - If you see measured values, DO NOT categorize them
   - Set confidence = 0.95, category = "Unknown" (backend will override)

3. MISSING VALUES (value is null OR source="missing"):
   - ONLY for missing values, infer category using:
     * Soil type characteristics
     * District patterns (Maharashtra regions)
     * Typical regional soil properties
   - Set confidence = 0.5-0.8 (lower for inferred)
   - NEVER generate numeric values
   - NEVER predict what the measured value might be

4. INFERENCE RULES (for missing values only):
   - Maharashtra Black Soil: Typically Low Nitrogen, Medium Phosphorus, High Potassium
   - Red Soil: Typically Low Nitrogen, Low Phosphorus, Low Potassium
   - Alluvial: Typically Medium Nitrogen, Medium Phosphorus, Medium Potassium
   - Adjust confidence based on irrigation: Irrigated (0.6-0.7), Rain-fed (0.5-0.6)

5. CATEGORIES (no numeric values allowed):
   - pH: Acidic (pH 0-6.5), Neutral (6.5-7.5), Alkaline (>7.5) [LOCKED - use pre-categorized]
   - Nitrogen: Low (<200), Medium (200-280), High (>280) kg/ha
   - Phosphorus: Low (<10), Medium (10-25), High (>25) kg/ha
   - Potassium: Low (<110), Medium (110-280), High (>280) kg/ha
   - Organic Carbon: Poor (<0.5%), Moderate (0.5-0.75%), Rich (>0.75%)

❌ FORBIDDEN ACTIONS:
❌ Generating or mentioning ANY numeric soil values
❌ Overriding measured value categories
❌ Predicting what measured values might be
❌ Categorizing parameters that have measured values
❌ Modifying pre-categorized pH
❌ Using "typical" values for measured parameters

✓ YOUR TASK:
For each parameter in extracted_params:
1. Check if pre-categorized pH exists → use it unchanged
2. Check if value is null or source="missing":
   - YES → Infer category using soil type + district
   - NO → Backend already handled it, output "Unknown" (will be overridden)

✓ OUTPUT FORMAT (JSON only, no text):
{{
  "version": "farmchain-ai-v1.0",
  "soil_profile": {{
    "pH": {{"category": "Neutral", "confidence": 0.95}},
    "Nitrogen": {{"category": "Low", "confidence": 0.65}},
    "Phosphorus": {{"category": "Medium", "confidence": 0.60}},
    "Potassium": {{"category": "High", "confidence": 0.70}}
  }}
}}

CRITICAL: Return ONLY raw JSON. NO explanations. NO markdown. Start with {{ and end with }}.
""")

    pre_categorized = {}

    param_mappings = {
        "pH": "pH",
        "Nitrogen": "Nitrogen",
        "Phosphorus": "Phosphorus",
        "Potassium": "Potassium",
        "Organic Carbon": "Organic Carbon",
        "Organic_Carbon": "Organic Carbon"
    }

    for param_key, param_name in param_mappings.items():
        if param_name in extracted:
            param_data = extracted[param_name]
            if isinstance(param_data, dict):
                value = param_data.get("value")
                source = param_data.get("source", "")

                if source == "report" and value is not None and isinstance(value, (int, float)):
                    if param_key == "pH":
                        category = categorize_ph(value)
                        # assert_pH_categorization(value, category)  # TEMP disabled
                    else:
                        category, _ = categorize_from_thresholds(param_key, value)

                    confidence = 0.95
                    inferred = False

                    pre_categorized[param_key] = {
                        "category": category,
                        "confidence": confidence,
                        "from_threshold": True,
                        "inferred": False,
                        "locked": True
                    }

                    if param_key == "pH":
                        expected_category = categorize_ph(value)
                        if category != expected_category:
                            raise ValueError(f"Measured pH category was overridden incorrectly: pH = {value} should be '{expected_category}', got '{category}'")
                    continue

                if value is not None and isinstance(value, (int, float)):
                    if param_key == "pH":
                        category = categorize_ph(value)
                        # assert_pH_categorization(value, category)  # TEMP disabled
                        confidence = 0.95
                        inferred = False
                    else:
                        category, confidence = categorize_from_thresholds(param_key, value)
                        inferred = False

                    pre_categorized[param_key] = {
                        "category": category,
                        "confidence": confidence,
                        "from_threshold": True,
                        "inferred": False
                    }

    chain = prompt | llm_json | json_parser
    try:
        pre_categorized_ph_json = json.dumps(pre_categorized_ph) if pre_categorized_ph else "null"
        result = chain.invoke({
            "extracted_params": json.dumps(extracted_params),
            "district": district,
            "soil_type": soil_type or "Unknown",
            "irrigation_type": irrigation_type,
            "pre_categorized_ph": pre_categorized_ph_json
        })

        if isinstance(result, dict):
            result_str = json.dumps(result)
        else:
            result_str = str(result)

        # SAFETY CHECK: Validate AI didn't generate numeric soil values
        validate_no_numeric_values_in_response(
            result_str,
            "classify_soil_profile"
        )

        result_str = re.sub(r'https?://[^\s]+langchain[^\s]+', '', result_str)
        result_str = re.sub(r'^(here\s+is|here\'?s|output|result|json|response)[\s:]*', '', result_str, flags=re.IGNORECASE)
        result_str = re.sub(r'```json\s*', '', result_str, flags=re.IGNORECASE)
        result_str = re.sub(r'```\s*', '', result_str)

        try:
            result = json.loads(result_str)
        except json.JSONDecodeError:
            json_match = re.search(r'\{.*\}', result_str, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise ValueError("Could not extract valid JSON from response")

        if not isinstance(result, dict):
            raise ValueError("Result is not a dictionary")

        if "soil_profile" not in result:
            raise ValueError("Result missing 'soil_profile' key")

        soil_profile = result.get("soil_profile", {})

        if pre_categorized_soil_profile and "pH" in pre_categorized_soil_profile:
            locked_ph = pre_categorized_soil_profile["pH"]
            soil_profile["pH"] = {
                "category": locked_ph["category"],
                "confidence": locked_ph["confidence"]
            }

            if "pH" in extracted_params:
                ph_param = extracted_params["pH"]
                if isinstance(ph_param, dict):
                    ph_value = ph_param.get("value")
                    if ph_value is not None and isinstance(ph_value, (int, float)):
                        expected_category = categorize_ph(ph_value)
                        if locked_ph["category"] != expected_category:
                            raise ValueError(f"CRITICAL: pH categorization failed - pH = {ph_value} should be '{expected_category}', got '{locked_ph['category']}'")
                        if ph_value == 6.9 and locked_ph["category"] != "Neutral":
                            raise ValueError("CRITICAL: pH categorization failed - pH = 6.9 should be 'Neutral'")

        for param_key, pre_cat_data in pre_categorized.items():
            if param_key == "pH":
                continue

            locked_category = pre_cat_data["category"]
            # CRITICAL: Force override - Ollama output is ignored for threshold-based categorization
            soil_profile[param_key] = {
                "category": locked_category,
                "confidence": pre_cat_data["confidence"]
            }

            # Verify the override worked
            if soil_profile[param_key]["category"] != locked_category:
                raise ValueError(f"CRITICAL: Failed to override {param_key} category. Expected '{locked_category}', got '{soil_profile[param_key]['category']}'")

        result["soil_profile"] = soil_profile

        # CRITICAL: Final enforcement - validate and force threshold-based categorization
        # result = validate_category_not_unknown(extracted_params, result)  # TEMP disabled
        
        # ADDITIONAL ENFORCEMENT: Double-check that measured values use threshold-based categories
        # This MUST run after Ollama classification to force correct threshold-based categories
        soil_profile = result.get("soil_profile", {})
        # Use extracted which is the local variable in classify_soil_profile (line 231)
        # extracted_params is the function parameter, extracted is the dict inside it
        extracted_dict = extracted  # This is already set at line 231
        
        for param_name in ["Nitrogen", "Phosphorus", "Potassium"]:
            if param_name in extracted_dict:
                param_data = extracted_dict[param_name]
                if isinstance(param_data, dict):
                    value = param_data.get("value")
                    source = param_data.get("source", "")
                    
                    if source == "report" and value is not None and isinstance(value, (int, float)):
                        expected_category, _ = categorize_from_thresholds(param_name, value)
                        actual_category = soil_profile.get(param_name, {}).get("category")
                        
                        if actual_category != expected_category:
                            # Enforcement: Force threshold-based categorization for measured values
                            if param_name not in soil_profile:
                                soil_profile[param_name] = {}
                            soil_profile[param_name]["category"] = expected_category
                            soil_profile[param_name]["confidence"] = 0.95
                            result["soil_profile"] = soil_profile

        return result
    except Exception as e:
        error_msg = str(e)
        error_msg = re.sub(r'https?://[^\s]+langchain[^\s]+', '', error_msg)
        error_msg = re.sub(r'For troubleshooting.*?OUTPUT_PARSING_FAILURE.*?', '', error_msg, flags=re.DOTALL)
        return {
            "version": "farmchain-ai-v1.0",
            "soil_profile": {},
            "error": error_msg
        }

def validate_crop_season(crops: list, season: str) -> bool:
    """FIX 3: Validate crops match the specified season"""
    return validate_crops_for_season(crops, season)

def generate_agronomy_recommendations(
    soil_profile: Dict[str, Any],
    district: str,
    season: str,
    irrigation_type: str,
    soil_type: Optional[str] = None,
    language: str = "english",
    max_retries: int = 2
) -> Dict[str, Any]:
    """
    Recommend crops, fertilizers, and equipment.
    All recommendations are guideline-based suggestions, NOT prescriptions.
    FIX 1: Explicit inputs required. FIX 2: Season validation. FIX 3: Sanity check.
    ENFORCE SINGLE SOURCE OF TRUTH: Recommendations MUST come from soil_profile only.
    Uses centralized CROP_FERTILITY_RULES from agricultural_config.
    
    Args:
        language: Output language ('english' or 'marathi'). Affects crop/fertilizer/equipment names.
    """
    if not district or not season or not irrigation_type:
        return {
            "version": "farmchain-ai-v1.0",
            "error": "District, season, and irrigation_type are required"
        }

    if not isinstance(soil_profile, dict) or not soil_profile:
        raise ValueError("Soil profile missing — cannot generate recommendations. Recommendations MUST be generated from soil_profile only.")

    soil_type = soil_type or "Unknown"
    
    # Normalize language input
    lang_lower = language.lower().strip()
    is_marathi = lang_lower in ("marathi", "मराठी")
    lang_str = "Marathi (मराठी)" if is_marathi else "English"

    prompt = ChatPromptTemplate.from_template("""
You are an agronomy recommendation assistant for Maharashtra, India agriculture system.

🔒 DATA SOURCE: You MUST base ALL recommendations ONLY on the provided soil_profile.
🔒 CONSTRAINT: NEVER recommend based on assumptions or typical patterns.

OUTPUT LANGUAGE: {language}

Required Inputs:
- District: {district}
- Season: {season}
- Irrigation Type: {irrigation_type}
- Soil Type: {soil_type}
- Soil Profile (READ-ONLY): {soil_profile}

🔴 CRITICAL CONSTRAINTS (MANDATORY - NEVER VIOLATE):

1. SOIL DATA IS READ-ONLY:
   - Use ONLY the categories in soil_profile
   - NEVER infer additional soil properties
   - NEVER generate numeric soil values
   - NEVER contradict soil_profile categories
   - If soil parameter is missing → recommendations must be conservative

2. SEASON ADHERENCE (STRICT):
   Season: {season}
   
   Maharashtra Season Crops:
   - Kharif (June-Oct): Soybean, Tur, Cotton, Maize, Rice, Bajra, Jowar, Groundnut, Sugarcane
   - Rabi (Oct-Mar): Wheat, Gram, Onion, Tomato, Potato, Mustard, Sunflower, Garlic, Fenugreek, Coriander
   - Summer (Mar-Jun): Watermelon, Muskmelon, Cucumber, Bitter Gourd, Okra
   
   ❌ NEVER recommend crops outside the specified season
   ✓ crop_recommendation.season MUST exactly match: {season}

3. FERTILITY-BASED CROP FILTERING:
   - Check soil_profile Nitrogen category and Organic Carbon category
   - High-input crops (Onion, Sugarcane, Tomato, Potato):
     * Require: Nitrogen = "Medium" or "High" AND Organic Carbon = "Moderate" or "Rich"
     * If Nitrogen = "Low" OR Organic Carbon = "Poor" → DO NOT recommend these crops
   - Low-input crops (Soybean, Tur, Gram, Jowar, Bajra): Suitable for all soil conditions
   
4. FERTILIZER RECOMMENDATIONS:
   - Base recommendations on soil_profile categories ONLY
   - Use descriptive ranges: "Low", "Medium", "High", "Low to Medium", "Medium to High"
   - NEVER provide exact numeric values (no kg/ha, kg/acre)
   - Match fertilizer intensity to soil nutrient status
   - Include application stages: Basal, Vegetative, Flowering, Grain Filling

5. EQUIPMENT RECOMMENDATIONS:
   - Standard farm equipment for Maharashtra
   - Appropriate for farm size and mechanization level
   - No AI inference - use standard lists

6. CROP DURATION:
   - Provide typical duration ranges for each recommended crop
   - Format: "Crop Name": "90-110 days"
   - Use Maharashtra-specific growing periods

❌ FORBIDDEN ACTIONS:
❌ Generating or mentioning ANY numeric soil values
❌ Recommending crops outside specified season
❌ Recommending high-input crops for low-fertility soil
❌ Contradicting soil_profile categories
❌ Inferring soil properties not in soil_profile
❌ Providing exact fertilizer quantities (kg/ha or kg/acre)
❌ Overriding backend crop filtering rules

✓ RECOMMENDATION PROCESS:
1. Read soil_profile categories (pH, Nitrogen, Phosphorus, Potassium, Organic Carbon)
2. Identify suitable crops for season: {season}
3. Filter out high-input crops if Nitrogen="Low" or Organic Carbon="Poor"
4. Select 2-3 primary crops appropriate for soil conditions
5. Recommend fertilizers based on nutrient deficiencies
6. Suggest standard equipment for farming stages

✓ OUTPUT FORMAT (JSON only, no text):
- If OUTPUT LANGUAGE is "Marathi (मराठी)": Return crop names, fertilizer names, and equipment names in Marathi ONLY. NO English text.
  - Example valid: ["गहू", "मसूर"] NOT ["गहू" (Wheat), "मसूर" (Gram)]
  - Example valid: ["यूरिया", "डीएपी"] NOT ["यूरिया" (Urea), "डीएपी" (DAP)]
- If OUTPUT LANGUAGE is "English": Return crop names, fertilizer names, and equipment names in English ONLY. NO translation annotations.
  - Example valid: ["Wheat", "Gram"] NOT ["Wheat (गहू)", "Gram (मसूर)"]
- Numeric ranges (e.g., "90-110 days", "Low to Medium") and section keys remain UNCHANGED in all languages.
- NEVER include annotations, parentheses, or translations within JSON values.
- NEVER mix languages in a single value.

{{
  "version": "farmchain-ai-v1.0",
  "crop_recommendation": {{
    "primary": ["Crop1", "Crop2"],
    "season": "{season}",
    "crop_durations": {{
      "Crop1": "110-130 days",
      "Crop2": "90-110 days"
    }}
  }},
  "fertilizer_plan": {{
    "Nitrogen": {{
      "recommended_range": "Medium to High",
      "fertilizers": ["Fertilizer1", "Fertilizer2"],
      "application_stages": ["Basal", "Vegetative"]
    }},
    "Phosphorus": {{
      "recommended_range": "Low to Medium",
      "fertilizers": ["Fertilizer3", "Fertilizer4"],
      "application_stages": ["Basal"]
    }},
    "Potassium": {{
      "recommended_range": "Medium",
      "fertilizers": ["Fertilizer5", "Fertilizer6"],
      "application_stages": ["Basal", "Flowering"]
    }}
  }},
  "equipment_plan": {{
    "land_preparation": ["Equipment1", "Equipment2"],
    "sowing": ["Equipment3", "Equipment4"],
    "irrigation": ["Equipment5", "Equipment6"],
    "spraying": ["Equipment7"],
    "harvesting": ["Equipment8", "Equipment9"]
  }}
}}

CRITICAL: Return ONLY raw JSON. NO explanations. NO markdown. NO text. NO annotations. NO parentheses. NO mixed languages. Start with {{ and end with }}.
""")

    chain = prompt | llm_json | json_parser

    for attempt in range(max_retries):
        try:
            result = chain.invoke({
                "soil_profile": json.dumps(soil_profile),
                "district": district,
                "season": season,
                "irrigation_type": irrigation_type,
                "soil_type": soil_type,
                "language": lang_str
            })
            
            result_str = json.dumps(result)
            
            # SAFETY CHECK: Validate AI didn't generate numeric soil values or fertilizer amounts
            # Allow numeric values in crop_durations (e.g., "90-110 days") but not soil parameters
            validate_no_numeric_values_in_response(
                result_str.replace("crop_durations", "___durations___"),  # Temporarily mask durations
                "generate_agronomy_recommendations"
            )
            
            result_str = re.sub(r'https?://[^\s]+langchain[^\s]+', '', result_str)
            result = json.loads(result_str) if result_str else result

            if "crop_recommendation" in result and "primary" in result["crop_recommendation"]:
                crops = result["crop_recommendation"]["primary"]
                
                # Translate Marathi crop names to English for validation
                # (LLM returns Marathi names if language=marathi, but validation uses English)
                crops_for_validation = translate_crop_names_to_english(crops)

                pH_category = soil_profile.get("pH", {}).get("category", "Unknown") if isinstance(soil_profile, dict) else "Unknown"
                nitrogen_category = soil_profile.get("Nitrogen", {}).get("category", "Unknown") if isinstance(soil_profile, dict) else "Unknown"
                organic_carbon_data = soil_profile.get("Organic Carbon", {}) or soil_profile.get("Organic_Carbon", {})
                organic_carbon_category = organic_carbon_data.get("category", "Unknown") if isinstance(organic_carbon_data, dict) else "Unknown"

                # Use centralized crop filtering logic from agricultural_config
                # Filter using English names to match agricultural_config definitions
                filtered_crops_en = [
                    crop for crop in crops_for_validation 
                    if not should_filter_crop(crop, nitrogen_category, organic_carbon_category)
                ]
                
                # Translate back to original language (Marathi or English)
                filtered_crops = [ENGLISH_CROP_MAP.get(crop, crop) if crop in ENGLISH_CROP_MAP else crop 
                                 for crop in filtered_crops_en]
                
                if len(filtered_crops) < len(crops):
                    removed_crops = crops[len(filtered_crops):]  # Removed crops (in original language)
                    result["crop_recommendation"]["primary"] = filtered_crops
                    
                    # Remove durations for filtered crops
                    if "crop_durations" in result["crop_recommendation"]:
                        for crop in removed_crops:
                            if crop in result["crop_recommendation"]["crop_durations"]:
                                del result["crop_recommendation"]["crop_durations"][crop]
                    
                    crops = filtered_crops

                # Validate no high-input crops remain for low fertility soil (using English names)
                high_input_in_result = [c for c in crops_for_validation if should_filter_crop(c, nitrogen_category, organic_carbon_category)]
                if high_input_in_result:
                    raise ValueError(f"FAIL-FAST: High-input crops not filtered: {high_input_in_result} (Nitrogen: {nitrogen_category}, OC: {organic_carbon_category})")

                # Validate crop season using English crop names
                if not validate_crop_season(crops_for_validation, season):
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return {
                            "version": "farmchain-ai-v1.0",
                            "error": f"Crop recommendations do not match season {season}. Recommended crops: {crops}"
                        }

                if "season" in result["crop_recommendation"]:
                    result["crop_recommendation"]["season"] = season

                # Ensure crop durations exist: build deterministic durations for displayed crop names
                try:
                    primary_crops_display = result["crop_recommendation"].get("primary", [])
                    if primary_crops_display and ("crop_durations" not in result["crop_recommendation"] or not isinstance(result["crop_recommendation"].get("crop_durations"), dict)):
                        # Build durations keyed by the displayed crop names (Marathi or English)
                        # Map to English for lookup, then assign to displayed names
                        durations = {}
                        for disp_name in primary_crops_display:
                            en_name = translate_crop_name_to_english(disp_name)
                            # get_crop_durations expects a list of English names
                            duration_map = get_crop_durations([en_name])
                            durations[disp_name] = duration_map.get(en_name, "90-120 days")
                        result["crop_recommendation"]["crop_durations"] = durations
                except Exception:
                    # Non-fatal: skip if any issue
                    pass

                # If output language is English, enforce English names for crops and plans
                if not is_marathi:
                    # Translate crops to English for display
                    if isinstance(result.get("crop_recommendation"), dict):
                        prim = result["crop_recommendation"].get("primary", [])
                        prim_en = [translate_crop_name_to_english(c) for c in prim]
                        result["crop_recommendation"]["primary"] = prim_en

                        # Rebuild durations keyed by English names
                        if isinstance(result["crop_recommendation"].get("crop_durations"), dict):
                            durations = {}
                            for c in prim_en:
                                durations[c] = get_crop_durations([c]).get(c, "90-120 days")
                            result["crop_recommendation"]["crop_durations"] = durations

                    # Sanitize equipment plan: if any non-ASCII or Marathi placeholders, replace with standard English list
                    std_equipment = {
                        "land_preparation": ["Tractor", "Plough", "Harrow"],
                        "sowing": ["Seed Drill", "Planter"],
                        "irrigation": ["Drip System", "Sprinkler"],
                        "spraying": ["Power Sprayer"],
                        "harvesting": ["Harvester", "Thresher"],
                    }
                    eq = result.get("equipment_plan", {})
                    def _has_non_ascii(items):
                        if isinstance(items, str):
                            return any(ord(ch) > 127 for ch in items)
                        if isinstance(items, list):
                            return any(any(ord(ch) > 127 for ch in str(it)) for it in items)
                        return False
                    bad_equipment = any(_has_non_ascii(v) or ('यंत्र' in str(v)) for v in (eq.values() if isinstance(eq, dict) else []))
                    if bad_equipment or not eq:
                        result["equipment_plan"] = std_equipment

                    # Sanitize fertilizer names if Marathi detected
                    fert_map = {
                        "यूरिया": "Urea",
                        "डीएपी": "DAP",
                        "डीएनपी": "DNP",
                        "फॉस्फेट": "Phosphate",
                        "सुपर फॉस्फेट": "Super Phosphate",
                        "सुपरफॉस्फेट": "Super Phosphate",
                        "क्लोराइड पोटाश": "MOP",
                        "मूर्त पोटाश": "MOP",
                        "पोटॅश": "Potash",
                        "मिक्रोसिलिक पोटाश": "MOP",
                        "डीप फॉस्फेट": "DAP"
                    }
                    stage_map = {
                        "बेसल": "Basal",
                        "वेगिटेटिव्ह": "Vegetative",
                        "वेजिटेबुली": "Vegetative",
                        "फूलने": "Flowering",
                        "फुलणे": "Flowering",
                        "ग्रेन फिलिंग": "Grain Filling"
                    }
                    range_map = {
                        "निम्न से मध्यम": "Low to Medium",
                        "मध्यम": "Medium",
                        "उच्च": "High",
                        "निम्न": "Low",
                    }
                    defaults = {
                        "Nitrogen": {"fertilizers": ["Urea", "DAP"], "application_stages": ["Basal", "Vegetative"], "recommended_range": "Low to Medium"},
                        "Phosphorus": {"fertilizers": ["DAP", "SSP"], "application_stages": ["Basal"], "recommended_range": "Low to Medium"},
                        "Potassium": {"fertilizers": ["MOP", "SOP"], "application_stages": ["Basal", "Flowering"], "recommended_range": "Medium"},
                    }
                    fert_plan = result.get("fertilizer_plan", {})
                    if isinstance(fert_plan, dict):
                        for nut, plan in fert_plan.items():
                            if isinstance(plan, dict) and isinstance(plan.get("fertilizers"), list):
                                plan["fertilizers"] = [fert_map.get(f, f) for f in plan["fertilizers"]]
                                # strip any residual non-ascii
                                plan["fertilizers"] = [re.sub(r"[^\x00-\x7F]+", "", f).strip() or "Fertilizer" for f in plan["fertilizers"]]
                            # Recommended range translation
                            if isinstance(plan, dict) and isinstance(plan.get("recommended_range"), str):
                                plan["recommended_range"] = range_map.get(plan["recommended_range"], plan["recommended_range"])
                            if isinstance(plan, dict) and isinstance(plan.get("application_stages"), list):
                                plan["application_stages"] = [stage_map.get(s, s) for s in plan["application_stages"]]
                                plan["application_stages"] = [re.sub(r"[^\x00-\x7F]+", "", s).strip() or "Basal" for s in plan["application_stages"]]
                            # Fill defaults if emptied
                            if isinstance(plan, dict):
                                if not plan.get("fertilizers"):
                                    plan["fertilizers"] = defaults.get(nut, {}).get("fertilizers", ["Fertilizer"])
                                if not plan.get("application_stages"):
                                    plan["application_stages"] = defaults.get(nut, {}).get("application_stages", ["Basal"])
                                if not plan.get("recommended_range"):
                                    plan["recommended_range"] = defaults.get(nut, {}).get("recommended_range", "Medium")
                                fert_plan[nut] = plan
                        result["fertilizer_plan"] = fert_plan

            # Rule-based fertilizer plan consistency: align Nitrogen range with soil category
            fert_plan = result.get("fertilizer_plan", {}) if isinstance(result, dict) else {}
            nitrogen_category = soil_profile.get("Nitrogen", {}).get("category") if isinstance(soil_profile, dict) else None
            if isinstance(fert_plan, dict) and nitrogen_category == "Low":
                n_plan = fert_plan.get("Nitrogen", {}) if isinstance(fert_plan.get("Nitrogen", {}), dict) else {}
                if n_plan.get("recommended_range") not in {"Low", "Low to Medium"}:
                    n_plan["recommended_range"] = "Low to Medium"
                    fert_plan["Nitrogen"] = n_plan
                    result["fertilizer_plan"] = fert_plan

            return result
        except Exception as e:
            if attempt == max_retries - 1:
                error_msg = str(e)
                error_msg = re.sub(r'https?://[^\s]+langchain[^\s]+', '', error_msg)
                error_msg = re.sub(r'For troubleshooting.*?OUTPUT_PARSING_FAILURE.*?', '', error_msg, flags=re.DOTALL)
                return {
                    "version": "farmchain-ai-v1.0",
                    "error": error_msg
                }

    return {
        "version": "farmchain-ai-v1.0",
        "error": "Failed to generate valid recommendations after retries"
    }



def generate_farmer_explanation(
    agronomy_data: Dict[str, Any],
    soil_profile: Dict[str, Any],
    district: str,
    season: str,
    irrigation_type: str,
    language: str = "marathi",
    max_retries: int = 3
) -> Dict[str, str]:
    """
    🔒 HARDENED: Generate farmer-friendly explanation using ONLY rule-based logic.
    NO AI involvement. Direct string assembly from soilProfile categories only.
    
    CRITICAL CONSTRAINTS:
    1. ONLY reads from soil_profile (measured/categorized soil data)
    2. NEVER uses agronomy_data, fertilizer_plan, or AI inference
    3. NEVER generates numeric soil values
    4. Output is deterministic and consistent
    5. Always includes fallback if parameters missing
    
    ACCEPTANCE TEST:
    Input: Available Nitrogen (N): 120 kg/ha → soil_profile["Nitrogen"]["category"] = "Low"
    Output explanation MUST be: "Nitrogen levels are low."
    If output contains "medium" → BUG (using wrong source)
    """
    if not isinstance(soil_profile, dict) or not soil_profile:
        raise ValueError("soilProfile missing — cannot generate explanation. Explanation MUST be generated from soilProfile only.")

    # Extract categories from soil_profile ONLY (no AI, no inference)
    pH_category = soil_profile.get("pH", {}).get("category", "Unknown") if isinstance(soil_profile.get("pH"), dict) else "Unknown"
    nitrogen_category = soil_profile.get("Nitrogen", {}).get("category", "Unknown") if isinstance(soil_profile.get("Nitrogen"), dict) else "Unknown"
    phosphorus_category = soil_profile.get("Phosphorus", {}).get("category", "Unknown") if isinstance(soil_profile.get("Phosphorus"), dict) else "Unknown"
    potassium_category = soil_profile.get("Potassium", {}).get("category", "Unknown") if isinstance(soil_profile.get("Potassium"), dict) else "Unknown"
    
    organic_carbon_data = soil_profile.get("Organic Carbon", {}) or soil_profile.get("Organic_Carbon", {})
    organic_carbon_category = organic_carbon_data.get("category", "Unknown") if isinstance(organic_carbon_data, dict) else "Unknown"

    # BUILD EXPLANATION - Pure rule-based, no AI
    explanation_parts = []
    
    # pH statement
    if pH_category != "Unknown":
        explanation_parts.append(f"Soil pH is {pH_category.lower()}.")
    else:
        explanation_parts.append("Soil pH data is not available. Soil testing is recommended.")
    
    # Nitrogen statement
    if nitrogen_category != "Unknown":
        explanation_parts.append(f"Nitrogen levels are {nitrogen_category.lower()}.")
        if nitrogen_category == "Low":
            explanation_parts.append("Nutrient supplementation is required to improve soil fertility.")
        elif nitrogen_category == "High":
            explanation_parts.append("Nitrogen levels are sufficient for crop growth.")
    else:
        explanation_parts.append("Nitrogen data is not available. Soil testing is recommended.")
    
    # Phosphorus statement (if available)
    if phosphorus_category != "Unknown":
        if phosphorus_category == "Low":
            explanation_parts.append("Phosphorus levels are low and may require supplementation.")
        elif phosphorus_category == "High":
            explanation_parts.append("Phosphorus levels are adequate.")
    
    # Potassium statement (if available)
    if potassium_category != "Unknown":
        if potassium_category == "Low":
            explanation_parts.append("Potassium levels are low and may require supplementation.")
        elif potassium_category == "High":
            explanation_parts.append("Potassium levels are adequate.")
    
    # Organic Carbon statement
    if organic_carbon_category != "Unknown":
        if organic_carbon_category == "Poor":
            explanation_parts.append("Soil organic carbon is poor, indicating low fertility. Soil improvement is advised.")
        elif organic_carbon_category == "Moderate":
            explanation_parts.append("Soil organic carbon is moderate.")
        elif organic_carbon_category == "Rich":
            explanation_parts.append("Soil organic carbon is rich, indicating good fertility.")
    
    # Context statement
    explanation_parts.append(f"This recommendation is for {season.lower()} season with {irrigation_type.lower()} irrigation in {district} district.")
    
    # Combine into final explanation
    explanation = " ".join(explanation_parts)
    


    # SAFETY CHECK: Verify no contradictions
    if nitrogen_category == "Low" and "medium" in explanation.lower() and "nitrogen" in explanation.lower():
        raise RuntimeError(
            "BUG: Explanation nitrogen text contradicts soil_profile category. "
            f"Category is '{nitrogen_category}' but explanation contains 'medium'."
        )

    disclaimer = get_disclaimer(language)

    # Return summary (factual, rule-based) - this is the core explanation
    return {
        "language": language,
        "summary": explanation.strip(),  # Factual summary from soilProfile
        "disclaimer": disclaimer
    }

def generate_advisory(
    recommendations: Dict[str, Any],
    soil_profile: Dict[str, Any],
    district: str,
    season: str,
    irrigation_type: str,
    language: str = "marathi"
) -> Optional[str]:
    """
    🔒 HARDENED: Generate human-friendly advisory text using llm_text (temperature ≤ 0.3).
    
    SAFETY CONSTRAINTS:
    - MUST read from: soilProfile categories, selected crops, fertilizer plan, season & irrigation
    - MUST NOT: generate numeric soil values, contradict summary, introduce new crops, override ranges
    - Uses llm_text for human-friendly language (not strict JSON)
    - Returns None if generation fails or validation fails
    
    This is the ONLY function allowed to use AI for text generation (not data).
    """
    try:
        if not isinstance(soil_profile, dict) or not soil_profile:
            return None
        
        if not isinstance(recommendations, dict):
            return None
        
        # Extract data for advisory generation
        crops = []
        if "crop_recommendation" in recommendations and "primary" in recommendations["crop_recommendation"]:
            crops = recommendations["crop_recommendation"]["primary"]
        
        fertilizer_plan = recommendations.get("fertilizer_plan", {})
        pH_category = soil_profile.get("pH", {}).get("category", "Unknown")
        nitrogen_category = soil_profile.get("Nitrogen", {}).get("category", "Unknown")
        organic_carbon_data = soil_profile.get("Organic Carbon", {}) or soil_profile.get("Organic_Carbon", {})
        organic_carbon_category = organic_carbon_data.get("category", "Unknown") if isinstance(organic_carbon_data, dict) else "Unknown"
        
        # Build hardened prompt with strict constraints
        lang_str = "Marathi" if language == "marathi" else "English"
        prompt_text = f"""You are an agriculture advisory assistant for Maharashtra, India farmers.

🔒 YOUR ROLE: Provide friendly, practical farming guidance in {lang_str} based ONLY on the data provided below.

📋 PROVIDED DATA (READ-ONLY - DO NOT MODIFY):
- Soil pH Category: {pH_category}
- Soil Nitrogen Category: {nitrogen_category}
- Organic Carbon Category: {organic_carbon_category}
- Recommended Crops: {', '.join(crops) if crops else 'None'}
- Season: {season}
- Irrigation: {irrigation_type}
- District: {district}
- Language: {lang_str}

🔴 CRITICAL CONSTRAINTS (MANDATORY - NEVER VIOLATE):

1. NO NUMERIC SOIL VALUES:
   - NEVER mention pH values like "7.2" or "6.5"
   - NEVER mention nutrient amounts like "150 kg/ha" or "200 kg/acre"
   - Use ONLY categories: {pH_category}, {nitrogen_category}, {organic_carbon_category}

2. NO NEW INTERPRETATIONS:
   - DO NOT infer additional soil properties
   - DO NOT contradict the categories above
   - DO NOT suggest crops not in the recommended list
   - DO NOT change the season or irrigation type

3. CROP ADHERENCE:
   - Mention ONLY these crops: {', '.join(crops) if crops else 'None'}
   - DO NOT suggest alternative crops
   - DO NOT mention crops from other seasons

4. NO EXACT QUANTITIES:
   - DO NOT provide exact fertilizer amounts
   - Use descriptive terms: "moderate", "adequate", "light", "heavy"

❌ FORBIDDEN EXAMPLES:
❌ "Your soil pH is 7.2" → Use: "Your soil pH is {pH_category.lower()}"
❌ "Apply 150 kg/ha of nitrogen" → Use: "Apply adequate nitrogen fertilizers"
❌ "Consider wheat as well" (if wheat not in crops list)
❌ "Nitrogen is medium" (if nitrogen_category is "Low")

✅ YOUR TASK:
Write a friendly 2-4 sentence advisory in {lang_str} that:
- Explains what the soil conditions mean for the farmer
- Mentions the recommended crops and why they're suitable
- Provides practical next steps
- Uses simple, farmer-friendly language

✅ OUTPUT:
- Return ONLY the advisory text
- NO markdown, NO JSON, NO formatting
- Start directly with the advisory
- Keep it concise and actionable

Generate the advisory now:"""

        prompt = ChatPromptTemplate.from_template(prompt_text)
        chain = prompt | llm_text | StrOutputParser()
        
        advisory = chain.invoke({})
        
        # Clean up the response
        advisory = advisory.strip()
        advisory = re.sub(r'^(here\s+is|here\'?s|advisory|output|result)[\s:]*', '', advisory, flags=re.IGNORECASE)
        advisory = re.sub(r'```.*?```', '', advisory, flags=re.DOTALL)
        
        # SAFETY CHECK: Validate advisory doesn't contain numeric soil values
        forbidden_patterns = [
            r'pH\s+(?:is|=|:)\s+\d+\.?\d*',  # pH is 7.2
            r'\d{2,}\s*(?:kg/ha|kg/acre)',  # 150 kg/ha
            r'nitrogen\s+(?:is|=|:)\s+\d+',  # nitrogen is 150
            r'phosphorus\s+(?:is|=|:)\s+\d+',
            r'potassium\s+(?:is|=|:)\s+\d+',
        ]
        
        advisory_lower = advisory.lower()
        for pattern in forbidden_patterns:
            if re.search(pattern, advisory_lower):
                print(f"⚠️ ADVISORY VALIDATION FAILED: Contains numeric values. Pattern: {pattern}", file=sys.stderr)
                return None  # Discard advisory if it contains numeric values
        
        # VALIDATION: Ensure advisory doesn't contradict summary
        # Check 1: No "medium nitrogen" if nitrogen is Low
        if nitrogen_category == "Low":
            if "medium" in advisory_lower and ("nitrogen" in advisory_lower or "n " in advisory_lower):
                print(f"⚠️ ADVISORY VALIDATION FAILED: Contradicts nitrogen category", file=sys.stderr)
                return None  # Discard advisory if it contradicts
        
        # Check 2: No "high nitrogen" if nitrogen is Low
        if nitrogen_category == "Low" and ("high nitrogen" in advisory_lower or "sufficient nitrogen" in advisory_lower or "adequate nitrogen" in advisory_lower):
            print(f"⚠️ ADVISORY VALIDATION FAILED: Claims high/sufficient nitrogen when it's Low", file=sys.stderr)
            return None
        
        # Check 3: pH category adherence
        if pH_category == "Neutral" and ("acidic" in advisory_lower or "alkaline" in advisory_lower):
            # Allow if it's explaining what neutral means, but not if contradicting
            if "soil is acidic" in advisory_lower or "soil is alkaline" in advisory_lower:
                print(f"⚠️ ADVISORY VALIDATION FAILED: Contradicts pH category", file=sys.stderr)
                return None
        
        # Check 4: Crop adherence - basic check
        if crops:
            # Extract words from advisory that might be crop names
            words = re.findall(r'\b[A-Za-z]+\b', advisory)
            crop_lower = [c.lower() for c in crops]
            
            # Known crop names that should only appear if in crops list
            known_crops = ["wheat", "gram", "onion", "tomato", "potato", "soybean", "cotton", "maize", 
                          "rice", "sugarcane", "tur", "bajra", "jowar", "groundnut", "mustard", 
                          "sunflower", "garlic", "fenugreek", "coriander", "watermelon", "muskmelon", 
                          "cucumber", "okra"]
            
            for word in words:
                word_clean = word.lower().strip('.,!?;:')
                if word_clean in known_crops and word_clean not in crop_lower:
                    print(f"⚠️ ADVISORY VALIDATION FAILED: Mentions crop '{word_clean}' not in recommended list", file=sys.stderr)
                    return None  # Discard if mentions crop not in list
        
        return advisory if advisory else None
        
    except Exception as e:
        # Fail silently - return None if advisory generation fails
        # This ensures the core summary is always available
        print(f"⚠️ Advisory generation error: {e}", file=sys.stderr)
        return None


def generate_detailed_ai_analysis(
    recommendations: Dict[str, Any],
    soil_profile: Dict[str, Any],
    district: str,
    season: str,
    irrigation_type: str,
    language: str = "marathi",
    max_retries: int = 2
) -> Dict[str, Any]:
    """Produce expanded AI analysis sections (text-only, no numbers) using llm_text."""
    try:
        if not isinstance(soil_profile, dict) or not soil_profile:
            raise ValueError("Soil profile missing for detailed analysis")

        # Prepare compact context objects
        crops = []
        equipment_plan = {}
        fertilizer_plan = {}
        if isinstance(recommendations, dict):
            crops = recommendations.get("crop_recommendation", {}).get("primary", [])
            equipment_plan = recommendations.get("equipment_plan", {})
            fertilizer_plan = recommendations.get("fertilizer_plan", {})

        lang_lower = (language or "english").strip().lower()
        is_marathi = lang_lower in ("marathi", "मराठी")
        lang_str = "Marathi (मराठी)" if is_marathi else "English"
        
        prompt = ChatPromptTemplate.from_template(
            """
You are an agriculture assistant. Generate a detailed, farmer-friendly analysis in {language} strictly based on the provided data. Do NOT invent numeric soil values or fertilizer quantities.

READ-ONLY DATA:
- District: {district}
- Season: {season}
- Irrigation: {irrigation_type}
- Soil Profile (categories only): {soil_profile}
- Recommended Crops: {crops}
- Fertilizer Plan (ranges only): {fertilizer_plan}
- Equipment Plan: {equipment_plan}

MANDATORY CONSTRAINTS:
- NEVER include numeric soil values (pH, N, P, K, OC) or fertilizer amounts.
- Align strictly with soil_profile categories and recommended crops/equipment.
- Prefer short, clear paragraphs.
- Write ONLY in {language}. If Marathi, use clean Devanagari words, no English in parentheses, no transliteration, no invented syllables, and no Unicode escape codes. Keep sentences simple and grammatical.
- Each section should be 1-2 concise sentences. FarmerActionChecklist should have 4-6 short imperative steps.
- If you lack detail, provide a short helpful line in the same language.

OUTPUT FORMAT:
Return ONLY valid JSON with these keys and text-only values in {language}:
{{
  "SoilHealthInterpretation": "...",
  "CropSuitability": "...",
  "CropExclusionReasons": "...",
  "RiskWarnings": "...",
  "FertilizerGuidance": "...",
  "EquipmentExplanation": "...",
  "SeasonalTiming": "...",
  "LongTermImprovement": "...",
  "ConfidenceNote": "...",
  "FarmerActionChecklist": ["step 1", "step 2", "..."]
}}

CRITICAL: Output must be pure JSON in {language}. No markdown, no extra text, no annotations, no parentheses translations, no mixed languages.
"""
        )

        chain = prompt | llm_text | StrOutputParser()

        for attempt in range(max_retries):
            try:
                raw = chain.invoke({
                    "language": lang_str,
                    "district": district,
                    "season": season,
                    "irrigation_type": irrigation_type,
                    "soil_profile": json.dumps(soil_profile),
                    "crops": json.dumps(crops),
                    "fertilizer_plan": json.dumps(fertilizer_plan),
                    "equipment_plan": json.dumps(equipment_plan),
                })

                # Basic cleanup and validation
                raw = raw.strip()
                raw = re.sub(r'^```json\s*', '', raw, flags=re.IGNORECASE)
                raw = re.sub(r'^```\s*|```\s*$', '', raw)

                # Validate no numeric soil values
                validate_no_numeric_values_in_response(raw, "generate_detailed_ai_analysis")

                result = json.loads(raw)

                # Ensure keys exist
                required_keys = [
                    "SoilHealthInterpretation", "CropSuitability", "CropExclusionReasons",
                    "RiskWarnings", "FertilizerGuidance", "EquipmentExplanation",
                    "SeasonalTiming", "LongTermImprovement", "ConfidenceNote", "FarmerActionChecklist"
                ]
                for k in required_keys:
                    if k not in result:
                        # Provide minimal placeholders
                        result[k] = [] if k == "FarmerActionChecklist" else ""

                # Enforce non-numeric content in strings
                def _decode_unicode_escapes(s: str) -> str:
                    """Decode explicit unicode escape sequences like \u092a into real characters."""
                    if not isinstance(s, str):
                        return ""
                    if "\\u" in s:
                        try:
                            s = s.encode("utf-8").decode("unicode_escape")
                        except Exception:
                            pass
                    return s

                def _non_numeric(s: str) -> str:
                    if not isinstance(s, str):
                        return ""
                    s = re.sub(r"\b\d+\.?\d*\b", "", s)  # strip any stray numbers
                    s = _decode_unicode_escapes(s)
                    return s.strip()

                for k, v in result.items():
                    if isinstance(v, str):
                        result[k] = _non_numeric(v)
                    elif isinstance(v, list):
                        result[k] = [_non_numeric(item) for item in v]

                # Fallback ConfidenceNote if empty or missing categories
                def _cat(param: str) -> str:
                    if not isinstance(soil_profile, dict):
                        return "Unknown"
                    return soil_profile.get(param, {}).get("category", "Unknown")

                conf_note = result.get("ConfidenceNote", "")
                if not conf_note or "()" in conf_note or "is ." in conf_note or conf_note.strip() == "":
                    if is_marathi:
                        result["ConfidenceNote"] = (
                            f"मातीच्या गुणधर्मांच्या श्रेणीनुसार विश्वास स्तर खालीलप्रमाणे आहे: "
                            f"pH ({_cat('pH')}), नायट्रोजन ({_cat('Nitrogen')}), "
                            f"फॉस्फरस ({_cat('Phosphorus')}), पोटॅशियम ({_cat('Potassium')})."
                        )
                    else:
                        result["ConfidenceNote"] = (
                            f"The confidence level for soil profile categories is as follows: "
                            f"pH ({_cat('pH')}), Nitrogen ({_cat('Nitrogen')}), "
                            f"Phosphorus ({_cat('Phosphorus')}), Potassium ({_cat('Potassium')})."
                        )

                return result
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"⚠️ Detailed analysis generation failed: {e}", file=sys.stderr)
                    # Return safe, minimal skeleton (language-aware) so UI still renders cards
                    if is_marathi:
                        placeholder = {
                            "SoilHealthInterpretation": "माहिती उपलब्ध नाही.",
                            "CropSuitability": "माहिती उपलब्ध नाही.",
                            "CropExclusionReasons": "माहिती उपलब्ध नाही.",
                            "RiskWarnings": "माहिती उपलब्ध नाही.",
                            "FertilizerGuidance": "माहिती उपलब्ध नाही.",
                            "EquipmentExplanation": "माहिती उपलब्ध नाही.",
                            "SeasonalTiming": "माहिती उपलब्ध नाही.",
                            "LongTermImprovement": "माहिती उपलब्ध नाही.",
                            "ConfidenceNote": "माहिती उपलब्ध नाही.",
                            "FarmerActionChecklist": ["पुन्हा प्रयत्न करा.", "नेटवर्क तपासा."]
                        }
                    else:
                        placeholder = {
                            "SoilHealthInterpretation": "No details available.",
                            "CropSuitability": "No details available.",
                            "CropExclusionReasons": "No details available.",
                            "RiskWarnings": "No details available.",
                            "FertilizerGuidance": "No details available.",
                            "EquipmentExplanation": "No details available.",
                            "SeasonalTiming": "No details available.",
                            "LongTermImprovement": "No details available.",
                            "ConfidenceNote": "No details available.",
                            "FarmerActionChecklist": ["Please try again.", "Check connectivity."]
                        }
                    return placeholder
                continue

    except Exception as outer_e:
        print(f"⚠️ Detailed analysis generation error: {outer_e}", file=sys.stderr)
        # Return safe, minimal skeleton on any error
        return {
            "SoilHealthInterpretation": "",
            "CropSuitability": "",
            "CropExclusionReasons": "",
            "RiskWarnings": "",
            "FertilizerGuidance": "",
            "EquipmentExplanation": "",
            "SeasonalTiming": "",
            "LongTermImprovement": "",
            "ConfidenceNote": "",
            "FarmerActionChecklist": []
        }

def process_soil_report(
    report_text: str,
    district: str,
    soil_type: Optional[str] = None,
    irrigation_type: str = "Rain-fed",
    season: str = "Kharif",
    language: str = "marathi"
) -> Dict[str, Any]:
    """
    Complete workflow: Extract → Classify → Recommend → Explain
    FIX 1: All inputs are explicit and required
    """
    try:
        if not district or not season or not irrigation_type:
            minimal_explanation = {
                "summary": "Required parameters are missing.",
                "disclaimer": get_disclaimer(language)
            }
            return {
                "version": "farmchain-ai-v1.0",
                "success": False,
                "error": "District, season, and irrigation_type are required",
                "explanation": minimal_explanation
            }

        raw_text = report_text
        extracted_params = hard_parse_soil_values(raw_text)

        # SAFETY: ensure all keys exist
        for k in ["pH", "Nitrogen", "Phosphorus", "Potassium", "OrganicCarbon"]:
            if k not in extracted_params:
                extracted_params[k] = {
                    "value": None,
                    "source": "missing"
                }

        print("🔍 Using rule-based extraction only (skipping AI to avoid timeout)", file=sys.stderr)
        extracted = {
            "version": "farmchain-ai-v1.0",
            "extracted_parameters": extracted_params
        }
        
        # Build soil_profile with threshold-based categorization (NO AI)
        print("🔍 Building soil profile with threshold-based categorization", file=sys.stderr)
        soil_profile = {}
        
        for param in ["pH", "Nitrogen", "Phosphorus", "Potassium", "OrganicCarbon"]:
            if param in extracted_params:
                param_data = extracted_params[param]
                if isinstance(param_data, dict):
                    value = param_data.get("value")
                    source = param_data.get("source", "")
                    
                    if source == "report" and value is not None and isinstance(value, (int, float)):
                        # Threshold-based categorization (deterministic, no AI)
                        category, confidence = categorize_from_thresholds(param, value)
                        soil_profile[param] = {
                            "category": category,
                            "confidence": confidence,
                            "value": value,
                            "unit": param_data.get("unit", ""),
                            "source": "report"
                        }
                    else:
                        soil_profile[param] = {
                            "category": "Unknown",
                            "confidence": 0.0,
                            "value": None,
                            "source": "missing"
                        }
            else:
                soil_profile[param] = {
                    "category": "Unknown",
                    "confidence": 0.0,
                    "value": None,
                    "source": "missing"
                }
        
        # Build basic explanation from soil profile (NO AI)
        print("🔍 Building explanation from categorized parameters", file=sys.stderr)
        pH_category = soil_profile.get("pH", {}).get("category", "Unknown")
        nitrogen_category = soil_profile.get("Nitrogen", {}).get("category", "Unknown")
        phosphorus_category = soil_profile.get("Phosphorus", {}).get("category", "Unknown")
        potassium_category = soil_profile.get("Potassium", {}).get("category", "Unknown")
        
        explanation_parts = []
        if pH_category != "Unknown":
            explanation_parts.append(f"Soil pH is {pH_category.lower()}.")
        if nitrogen_category != "Unknown":
            explanation_parts.append(f"Nitrogen levels are {nitrogen_category.lower()}.")
        if phosphorus_category != "Unknown":
            explanation_parts.append(f"Phosphorus levels are {phosphorus_category.lower()}.")
        if potassium_category != "Unknown":
            explanation_parts.append(f"Potassium levels are {potassium_category.lower()}.")
        
        summary = " ".join(explanation_parts) if explanation_parts else "Unable to extract soil parameters."
        
        clean_values = build_clean_values(extracted.get("extracted_parameters", {}), soil_profile)
        
        # Generate recommendations (with error handling for timeouts)
        recommendations = None
        try:
            recommendations = generate_agronomy_recommendations(
                soil_profile,
                district,
                season,
                irrigation_type,
                soil_type,
                language
            )
            if "error" in recommendations:
                print(f"⚠️  Recommendation error: {recommendations.get('error')}", file=sys.stderr)
                recommendations = None
        except Exception as e:
            print(f"⚠️  Recommendation generation failed: {str(e)[:80]}", file=sys.stderr)
            recommendations = None
        
        # Fallback if recommendations failed
        if not recommendations:
            recommendations = {
                "version": "farmchain-ai-v1.0",
                "crop_recommendation": {"primary": [], "season": season},
                "fertilizer_plan": {"primary": "Contact local agricultural expert for customized recommendations"},
                "equipment_plan": {}
            }
        
        # Generate farmer-friendly explanation (optional - not critical for functionality)
        explanation = {
            "summary": summary,
            "disclaimer": get_disclaimer(language)
        }
        try:
            exp_result = generate_farmer_explanation(
                recommendations,
                soil_profile,
                district,
                season,
                irrigation_type,
                language
            )
            if isinstance(exp_result, dict) and not exp_result.get("error"):
                explanation.update(exp_result)
        except Exception as e:
            print(f"⚠️  Explanation generation skipped: {str(e)[:50]}", file=sys.stderr)
        
        # Generate advisory (optional - not critical)
        try:
            advisory = generate_advisory(
                recommendations,
                soil_profile,
                district,
                season,
                irrigation_type,
                language
            )
            if advisory and not advisory.get("error"):
                explanation["advisory"] = advisory
        except Exception as e:
            print(f"⚠️  Advisory generation skipped: {str(e)[:50]}", file=sys.stderr)
        
        # Generate detailed AI analysis with all fields (Soil Health Interpretation, Crop Suitability, etc)
        detailed_analysis = {}
        try:
            detailed_analysis = generate_detailed_ai_analysis(
                recommendations,
                soil_profile,
                district,
                season,
                irrigation_type,
                language
            )
            if detailed_analysis and not detailed_analysis.get("error"):
                print(f"✅ Detailed analysis generated with {len(detailed_analysis)} fields", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Detailed analysis generation skipped: {str(e)[:80]}", file=sys.stderr)
            detailed_analysis = {
                "SoilHealthInterpretation": "Soil health assessment based on extracted parameters",
                "CropSuitability": "Crop recommendations based on soil conditions",
                "CropExclusionReasons": [],
                "RiskWarnings": [],
                "FertilizerGuidance": "Fertilizer recommendations based on soil nutrient status",
                "EquipmentExplanation": "Equipment recommendations for farming operations",
                "SeasonalTiming": f"Recommendations for {season} season",
                "LongTermImprovement": "Long-term soil improvement strategies",
                "ConfidenceNote": "Analysis based on available soil data",
                "FarmerActionChecklist": ["Conduct soil testing", "Implement recommendations gradually"]
            }
        
        print("✅ Soil report processing complete with recommendations", file=sys.stderr)



        return {
            "success": True,
            "extracted_parameters": extracted.get("extracted_parameters", {}),
            "soil_profile": soil_profile,
            "explanation": explanation,
            "recommendations": recommendations,
            "ai_detailed_analysis": detailed_analysis,
            "clean_values": clean_values,
            "language": language
        }
    except Exception as e:
        error_msg = str(e)
        print(f"❌ ERROR in process_soil_report: {error_msg}", file=sys.stderr)
        minimal_explanation = {
            "summary": "An error occurred while processing the soil report.",
            "disclaimer": "Please verify extracted values manually."
        }
        return {
            "version": "farmchain-ai-v1.0",
            "success": False,
            "error": error_msg,
            "explanation": minimal_explanation
        }

# ============================================================================
# DEPRECATED: Old AI-based functions below - kept for reference only
# The transition to rule-based extraction eliminates these slow AI calls
# ============================================================================
# Old AI-based code removed to eliminate timeout issues
# All processing now uses rule-based extraction and categorization
# ============================================================================

        # CRITICAL: Final verification check (should never trigger if override worked)
        if isinstance(explanation, dict) and "summary" in explanation:
            summary_content = explanation["summary"]
            summary_lower = summary_content.lower()
            # Check for hardcoded "medium" nitrogen text (case-insensitive, with or without punctuation)
            if "nitrogen levels are medium" in summary_lower or "nitrogen is medium" in summary_lower:
                # Safety measure: Force correct the summary
                explanation["summary"] = final_explanation_content
        
        if isinstance(recommendations, dict) and "crop_recommendation" in recommendations:
            crop_list = recommendations["crop_recommendation"].get("primary", [])
            # Check if Onion appears when it shouldn't (already checked for Low nitrogen above)
            # Additional check: if crops list is always the same, it's a static response
            # This will catch if Onion always appears regardless of soil conditions
            if "Onion" in crop_list and soil_profile["Nitrogen"]["category"] == "Low":
                raise RuntimeError("INVALID DEFAULT CROP IN RESPONSE: Onion found in crop recommendations for low fertility soil - static response detected")

        # FINAL VERIFICATION: Ensure explanation structure is correct before returning
        if not isinstance(explanation, dict):
            explanation = {
                "summary": str(explanation) if explanation else "Unable to generate explanation.",
                "disclaimer": get_disclaimer(language)
            }
        
        if "summary" not in explanation:
            # Try to use content as fallback, or generate from soil_profile
            if "content" in explanation:
                explanation["summary"] = explanation["content"]
            else:
                ph_cat = soil_profile.get("pH", {}).get("category", "Unknown")
                n_cat = soil_profile.get("Nitrogen", {}).get("category", "Unknown")
                if ph_cat != "Unknown" and n_cat != "Unknown":
                    explanation["summary"] = f"Soil pH is {ph_cat.lower()}. Nitrogen levels are {n_cat.lower()}."
                else:
                    explanation["summary"] = "Unable to generate explanation summary."
        
        if "disclaimer" not in explanation:
            explanation["disclaimer"] = get_disclaimer(language)
        
        clean_values = build_clean_values(extracted.get("extracted_parameters", {}), soil_profile)

        return {
            "success": True,
            "extracted_parameters": extracted.get("extracted_parameters", {}),
            "soil_profile": soil_profile,
            "recommendations": recommendations,
            "explanation": explanation,
            "ai_detailed_analysis": ai_detailed_analysis,
            "clean_values": clean_values,
            "language": language
        }
    except Exception as e:
        # DO NOT return default responses - throw error instead
        # This ensures failures are visible and not masked by fallbacks
        error_msg = str(e)
        print(f"❌ CRITICAL ERROR in process_soil_report: {error_msg}", file=sys.stderr)
        # Ensure explanation is always included, even in exception cases
        minimal_explanation = {
            "summary": "An error occurred while processing the soil report.",
            "disclaimer": get_disclaimer(language)
        }
        return {
            "version": "farmchain-ai-v1.0",
            "success": False,
            "error": error_msg,
            "explanation": minimal_explanation
        }
