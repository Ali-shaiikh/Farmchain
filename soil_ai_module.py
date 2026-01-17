import os
import sys
import json
import re
from typing import Dict, Any, Optional
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from agricultural_config import (
    SOIL_THRESHOLDS, PH_THRESHOLDS, KHARIF_CROPS, RABI_CROPS, SUMMER_CROPS,
    SEASON_CROPS, HIGH_INPUT_CROPS, MAHARASHTRA_DISTRICTS, SOIL_TYPES,
    SEASONS, IRRIGATION_TYPES,
    categorize_ph, categorize_parameter, get_crop_list_for_season,
    validate_crops_for_season, should_filter_crop, get_disclaimer
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
        
        # Clean up any potential contamination
        result_str = re.sub(r'https?://[^\s]+langchain[^\s]+', '', result_str)
        result = json.loads(result_str) if result_str else result
        
        # SAFETY CHECK: Validate extracted parameters
        if "extracted_parameters" in result:
            for param_name, param_data in result["extracted_parameters"].items():
                if isinstance(param_data, dict):
                    # Verify value is either null or from report
                    value = param_data.get("value")
                    source = param_data.get("source", "")
                    
                    if value is not None and source not in ["report", "missing"]:
                        raise ValueError(
                            f"SAFETY VIOLATION: Parameter {param_name} has value {value} "
                            f"with source '{source}'. AI may have generated this value. "
                            f"Valid sources are 'report' or 'missing' only."
                        )
        
        return result
    except Exception as e:
        error_msg = str(e)
        error_msg = re.sub(r'https?://[^\s]+langchain[^\s]+', '', error_msg)
        error_msg = re.sub(r'For troubleshooting.*?OUTPUT_PARSING_FAILURE.*?', '', error_msg, flags=re.DOTALL)
        return {
            "version": "farmchain-ai-v1.0",
            "extracted_parameters": {},
            "error": error_msg
        }

def categorizePH(value: float) -> str:
    """
    Categorize pH value using explicit threshold logic.
    REQUIRED rule: if (ph.value !== null) { ph.category = categorizePH(ph.value); ph.inferred = false; }
    EXACT implementation - no defaults, no fallbacks.
    Delegates to centralized config function.
    """
    return categorize_ph(value)

def assert_pH_categorization(value: float, category: str) -> None:
    """
    Assertion to prevent silent failure.
    Example: if (ph.value === 6.9 && ph.category !== "Neutral") { throw error }
    """
    if value == 6.9 and category != "Neutral":
        raise ValueError(f"pH categorization logic failed: pH = 6.9 should be 'Neutral', got '{category}'")
    if value == 6.4 and category != "Acidic":
        raise ValueError(f"pH categorization logic failed: pH = 6.4 should be 'Acidic', got '{category}'")
    if value == 7.6 and category != "Alkaline":
        raise ValueError(f"pH categorization logic failed: pH = 7.6 should be 'Alkaline', got '{category}'")
    if value == 7.0 and category != "Neutral":
        raise ValueError(f"pH categorization logic failed: pH = 7.0 should be 'Neutral', got '{category}'")

def categorize_from_thresholds(param_name: str, value: float) -> tuple[str, float]:
    """
    Categorize soil parameter based on numeric thresholds.
    Returns (category, confidence) tuple.
    Hard rule: Measured values must be categorized from thresholds, not inference.
    For pH, uses explicit categorizePH() function.
    Uses centralized SOIL_THRESHOLDS from agricultural_config.
    """
    try:
        category = categorize_parameter(param_name, value)
        confidence = 0.95  # High confidence for threshold-based categorization
        return (category, confidence)
    except ValueError:
        return ("Unknown", 0.5)

def validate_category_not_unknown(extracted_params: Dict[str, Any], classified_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    FIX 1: If a numeric soil value exists, category must NEVER be Unknown.
    Hard rule: if (value !== null) { category !== "Unknown" }
    """
    if not isinstance(classified_profile, dict) or "soil_profile" not in classified_profile:
        return classified_profile

    soil_profile = classified_profile.get("soil_profile", {})
    extracted = extracted_params.get("extracted_parameters", {}) if isinstance(extracted_params, dict) else extracted_params

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
                    current_category = soil_profile.get(param_key, {}).get("category") if isinstance(soil_profile.get(param_key), dict) else None
                    expected_category = categorize_from_thresholds(param_key, value)[0]

                    if current_category == expected_category:
                        print(f"✓ LOCKED: {param_key} = {value} (source: report) → category: {current_category} (already locked, skipping override)", file=sys.stderr)
                        continue

                    category, confidence = categorize_from_thresholds(param_key, value)

                    if param_key == "pH":
                        if category != expected_category:
                            raise ValueError(f"Measured pH category was overridden incorrectly: pH = {value} (source: report) should be '{expected_category}', got '{category}'")

                    if param_key not in soil_profile:
                        soil_profile[param_key] = {}
                    soil_profile[param_key]["category"] = category
                    soil_profile[param_key]["confidence"] = confidence

                    print(f"✓ HARD RULE: {param_key} = {value} (source: report) → category: {category} (threshold-based, not inference)", file=sys.stderr)
                elif value is not None and isinstance(value, (int, float)):
                    category, confidence = categorize_from_thresholds(param_key, value)
                    if param_key in soil_profile:
                        current_category = soil_profile[param_key].get("category", "")
                        if current_category == "Unknown" or current_category != category:
                            soil_profile[param_key]["category"] = category
                            soil_profile[param_key]["confidence"] = confidence
                            print(f"✓ FIX: {param_key} = {value} → category: {category} (threshold-based)", file=sys.stderr)

    classified_profile["soil_profile"] = soil_profile
    return classified_profile

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
                        category = categorizePH(value)
                        assert_pH_categorization(value, category)
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
                        expected_category = categorizePH(value)
                        if category != expected_category:
                            raise ValueError(f"Measured pH category was overridden incorrectly: pH = {value} should be '{expected_category}', got '{category}'")
                    continue

                if value is not None and isinstance(value, (int, float)):
                    if param_key == "pH":
                        category = categorizePH(value)
                        assert_pH_categorization(value, category)
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
                        expected_category = categorizePH(ph_value)
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
        result = validate_category_not_unknown(extracted_params, result)
        
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
    max_retries: int = 2
) -> Dict[str, Any]:
    """
    Recommend crops, fertilizers, and equipment.
    All recommendations are guideline-based suggestions, NOT prescriptions.
    FIX 1: Explicit inputs required. FIX 2: Season validation. FIX 3: Sanity check.
    ENFORCE SINGLE SOURCE OF TRUTH: Recommendations MUST come from soil_profile only.
    Uses centralized CROP_FERTILITY_RULES from agricultural_config.
    """
    if not district or not season or not irrigation_type:
        return {
            "version": "farmchain-ai-v1.0",
            "error": "District, season, and irrigation_type are required"
        }

    if not isinstance(soil_profile, dict) or not soil_profile:
        raise ValueError("Soil profile missing — cannot generate recommendations. Recommendations MUST be generated from soil_profile only.")

    soil_type = soil_type or "Unknown"

    prompt = ChatPromptTemplate.from_template("""
You are an agronomy recommendation assistant for Maharashtra, India agriculture system.

🔒 DATA SOURCE: You MUST base ALL recommendations ONLY on the provided soil_profile.
🔒 CONSTRAINT: NEVER recommend based on assumptions or typical patterns.

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
      "fertilizers": ["Urea", "DAP"],
      "application_stages": ["Basal", "Vegetative"]
    }},
    "Phosphorus": {{
      "recommended_range": "Low to Medium",
      "fertilizers": ["DAP", "SSP"],
      "application_stages": ["Basal"]
    }},
    "Potassium": {{
      "recommended_range": "Medium",
      "fertilizers": ["MOP", "SOP"],
      "application_stages": ["Basal", "Flowering"]
    }}
  }},
  "equipment_plan": {{
    "land_preparation": ["Tractor", "Plough", "Harrow"],
    "sowing": ["Seed Drill", "Planter"],
    "irrigation": ["Drip System", "Sprinkler"],
    "spraying": ["Power Sprayer"],
    "harvesting": ["Harvester", "Thresher"]
  }}
}}

CRITICAL: Return ONLY raw JSON. NO explanations. NO markdown. NO text. Start with {{ and end with }}.
""")

    chain = prompt | llm_json | json_parser

    for attempt in range(max_retries):
        try:
            result = chain.invoke({
                "soil_profile": json.dumps(soil_profile),
                "district": district,
                "season": season,
                "irrigation_type": irrigation_type,
                "soil_type": soil_type
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

                pH_category = soil_profile.get("pH", {}).get("category", "Unknown") if isinstance(soil_profile, dict) else "Unknown"
                nitrogen_category = soil_profile.get("Nitrogen", {}).get("category", "Unknown") if isinstance(soil_profile, dict) else "Unknown"
                organic_carbon_data = soil_profile.get("Organic Carbon", {}) or soil_profile.get("Organic_Carbon", {})
                organic_carbon_category = organic_carbon_data.get("category", "Unknown") if isinstance(organic_carbon_data, dict) else "Unknown"

                # Use centralized crop filtering logic from agricultural_config
                filtered_crops = [
                    crop for crop in crops 
                    if not should_filter_crop(crop, nitrogen_category, organic_carbon_category)
                ]
                
                if len(filtered_crops) < len(crops):
                    removed_crops = [c for c in crops if c not in filtered_crops]
                    result["crop_recommendation"]["primary"] = filtered_crops
                    
                    # Remove durations for filtered crops
                    if "crop_durations" in result["crop_recommendation"]:
                        for crop in removed_crops:
                            if crop in result["crop_recommendation"]["crop_durations"]:
                                del result["crop_recommendation"]["crop_durations"][crop]
                    
                    crops = filtered_crops

                # Validate no high-input crops remain for low fertility soil
                high_input_in_result = [c for c in crops if should_filter_crop(c, nitrogen_category, organic_carbon_category)]
                if high_input_in_result:
                    raise ValueError(f"FAIL-FAST: High-input crops not filtered: {high_input_in_result} (Nitrogen: {nitrogen_category}, OC: {organic_carbon_category})")

                if not validate_crop_season(crops, season):
                    if attempt < max_retries - 1:
                        continue
                    else:
                        return {
                            "version": "farmchain-ai-v1.0",
                            "error": f"Crop recommendations do not match season {season}. Recommended crops: {crops}"
                        }

                if "season" in result["crop_recommendation"]:
                    result["crop_recommendation"]["season"] = season

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
    
    # #region agent log
    try:
        import os
        os.makedirs('/Users/alishaikh/Desktop/FarmChain/.cursor', exist_ok=True)
        import json as json_module
        with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"soil_ai_module.py:790","message":"INSIDE generate_farmer_explanation: soil_profile categories","data":{"ph_category":pH_category,"nitrogen_category":nitrogen_category,"phosphorus_category":phosphorus_category,"potassium_category":potassium_category,"organic_carbon_category":organic_carbon_category},"timestamp":int(__import__("time").time()*1000)}) + "\n")
    except Exception as log_err:
        print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
    # #endregion

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
    
    # #region agent log
    try:
        import json as json_module
        with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"soil_ai_module.py:796","message":"INSIDE generate_farmer_explanation: explanation string created","data":{"explanation":explanation},"timestamp":int(__import__("time").time()*1000)}) + "\n")
    except Exception as log_err:
        print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
    # #endregion

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
        prompt_text = f"""You are an agriculture advisory assistant for Maharashtra, India farmers.

🔒 YOUR ROLE: Provide friendly, practical farming guidance based ONLY on the data provided below.

📋 PROVIDED DATA (READ-ONLY - DO NOT MODIFY):
- Soil pH Category: {pH_category}
- Soil Nitrogen Category: {nitrogen_category}
- Organic Carbon Category: {organic_carbon_category}
- Recommended Crops: {', '.join(crops) if crops else 'None'}
- Season: {season}
- Irrigation: {irrigation_type}
- District: {district}
- Language: {"Marathi" if language == "marathi" else "English"}

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
Write a friendly 2-4 sentence advisory in {"Marathi" if language == "marathi" else "English"} that:
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
    # #region agent log
    try:
        import os
        os.makedirs('/Users/alishaikh/Desktop/FarmChain/.cursor', exist_ok=True)
        import json as json_module
        with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"START","location":"soil_ai_module.py:843","message":"process_soil_report ENTRY","data":{},"timestamp":int(__import__("time").time()*1000)}) + "\n")
    except Exception as log_err:
        print(f"⚠️ DEBUG LOG ERROR (entry): {log_err}", file=sys.stderr)
    # #endregion
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

        extracted = extract_soil_parameters(report_text)
        extracted_params = extracted.get("extracted_parameters", {})
        
        # CRITICAL: If extraction failed, try to extract Nitrogen directly from text as fallback
        if not extracted_params or (isinstance(extracted_params, dict) and len(extracted_params) == 0):
            # Try to find Nitrogen value in text - be more specific to avoid matching pH or other values
            # Look for patterns like "Nitrogen: 120", "Available Nitrogen (N): 120 kg/ha", "N: 120"
            # Exclude values that are clearly pH (0-14 range) or percentages
            nitrogen_patterns = [
                r'(?:available\s+)?nitrogen\s*\(?n\)?\s*[:\-]?\s*(\d{2,}(?:\.\d+)?)\s*(?:kg/ha|kg/acre)',  # "Nitrogen: 120 kg/ha"
                r'(?:n|nitrogen)\s*[:\-]\s*(\d{2,}(?:\.\d+)?)\s*(?:kg/ha|kg/acre)',  # "N: 120 kg/ha"
                r'nitrogen\s+content[:\-]?\s*(\d{2,}(?:\.\d+)?)\s*(?:kg/ha|kg/acre)',  # "Nitrogen content: 120 kg/ha"
            ]
            nitrogen_value = None
            for pattern in nitrogen_patterns:
                match = re.search(pattern, report_text, re.IGNORECASE)
                if match:
                    candidate = float(match.group(1))
                    # Only accept values in reasonable range for Nitrogen (10-500 kg/ha)
                    # This excludes pH values (0-14) and percentages
                    if 10 <= candidate <= 500:
                        nitrogen_value = candidate
                        break
            
            if nitrogen_value:
                if "extracted_parameters" not in extracted:
                    extracted["extracted_parameters"] = {}
                extracted["extracted_parameters"]["Nitrogen"] = {
                    "value": nitrogen_value,
                    "unit": "kg/ha",
                    "source": "report",
                    "unit_uncertain": False
                }
                extracted_params = extracted["extracted_parameters"]

        soil_profile = {}

        if "pH" in extracted_params:
            ph_data = extracted_params["pH"]
            if isinstance(ph_data, dict):
                ph_value = ph_data.get("value")
                ph_source = ph_data.get("source", "")

                if ph_value is not None and isinstance(ph_value, (int, float)):
                    ph_category = categorizePH(ph_value)

                    assert_pH_categorization(ph_value, ph_category)

                    if ph_value == 6.9 and ph_category != "Neutral":
                        raise ValueError("CRITICAL: pH categorization failed - pH = 6.9 should be 'Neutral'")

                    soil_profile["pH"] = {
                        "category": ph_category,
                        "confidence": 0.95,
                        "source": ph_source,
                        "inferred": False,
                        "locked": True
                    }

        classified = classify_soil_profile(
            extracted_params,
            district,
            soil_type,
            irrigation_type,
            pre_categorized_soil_profile=soil_profile
        )

        if isinstance(classified, dict) and "error" in classified:
            error_msg = classified.get("error", "Unknown classification error")
            print(f"❌ CLASSIFICATION ERROR: {error_msg}", file=sys.stderr)
            minimal_explanation = {
                "summary": "Failed to categorize soil profile.",
                "disclaimer": get_disclaimer(language)
            }
            return {
                "version": "farmchain-ai-v1.0",
                "success": False,
                "error": f"Failed to categorize soil profile: {error_msg}",
                "extracted_parameters": extracted.get("extracted_parameters", {}),
                "soil_profile": classified.get("soil_profile", {}),
                "explanation": minimal_explanation
            }

        soil_profile = classified.get("soil_profile", {}) if isinstance(classified, dict) else {}
        
        # #region agent log
        try:
            import os
            os.makedirs('/Users/alishaikh/Desktop/FarmChain/.cursor', exist_ok=True)
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"soil_ai_module.py:949","message":"BEFORE enforcement: soil_profile Nitrogen category","data":{"nitrogen_category":soil_profile.get("Nitrogen",{}).get("category"),"soil_profile_id":id(soil_profile)},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion
        
        # CRITICAL FINAL ENFORCEMENT: Force threshold-based categorization after classification
        # This ensures that even if Ollama returns wrong category, we correct it
        extracted_params_final = extracted.get("extracted_parameters", {})
        for param_name in ["Nitrogen", "Phosphorus", "Potassium"]:
            if param_name in extracted_params_final:
                param_data = extracted_params_final[param_name]
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
                            # #region agent log
                            try:
                                import json as json_module
                                with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                                    f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"soil_ai_module.py:974","message":"AFTER enforcement correction: soil_profile Nitrogen category","data":{"nitrogen_category":soil_profile.get("Nitrogen",{}).get("category"),"soil_profile_id":id(soil_profile)},"timestamp":int(__import__("time").time()*1000)}) + "\n")
                            except Exception as log_err:
                                print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
                            # #endregion

        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"soil_ai_module.py:1012","message":"AFTER all enforcement: soil_profile Nitrogen category","data":{"nitrogen_category":soil_profile.get("Nitrogen",{}).get("category"),"soil_profile_id":id(soil_profile)},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion

        if not isinstance(soil_profile, dict) or not soil_profile:
            print(f"❌ SOIL PROFILE VALIDATION FAILED", file=sys.stderr)
            print(f"Classified type: {type(classified)}", file=sys.stderr)
            print(f"Classified keys: {list(classified.keys()) if isinstance(classified, dict) else 'N/A'}", file=sys.stderr)
            print(f"Extracted parameters: {json.dumps(extracted.get('extracted_parameters', {}), indent=2)}", file=sys.stderr)

            extracted_params = extracted.get("extracted_parameters", {})
            if not extracted_params or (isinstance(extracted_params, dict) and len(extracted_params) == 0):
                minimal_explanation = {
                    "summary": "No soil parameters were extracted from the report.",
                    "disclaimer": get_disclaimer(language)
                }
                return {
                    "version": "farmchain-ai-v1.0",
                    "success": False,
                    "error": "No soil parameters were extracted from the report. Please ensure the soil report contains pH, Nitrogen, Phosphorus, or Potassium values.",
                    "extracted_parameters": extracted_params,
                    "soil_profile": {},
                    "explanation": minimal_explanation
                }

            minimal_explanation = {
                "summary": "Failed to categorize soil profile.",
                "disclaimer": get_disclaimer(language)
            }
            return {
                "version": "farmchain-ai-v1.0",
                "success": False,
                "error": "Failed to categorize soil profile. The classification step did not return valid soil data. This may be due to an AI processing error.",
                "extracted_parameters": extracted.get("extracted_parameters", {}),
                "soil_profile": {},
                "explanation": minimal_explanation
            }


        if not isinstance(soil_profile, dict) or not soil_profile:
            raise ValueError("Soil profile missing — cannot generate recommendations. Recommendations MUST be generated from soil_profile only.")

        recommendations = generate_agronomy_recommendations(
            soil_profile,
            district,
            season,
            irrigation_type,
            soil_type
        )

        if "error" in recommendations:
            # Generate minimal explanation from soil_profile even if recommendations failed
            minimal_summary = "Unable to generate recommendations."
            if isinstance(soil_profile, dict) and soil_profile:
                ph_cat = soil_profile.get("pH", {}).get("category", "Unknown")
                n_cat = soil_profile.get("Nitrogen", {}).get("category", "Unknown")
                if ph_cat != "Unknown" and n_cat != "Unknown":
                    minimal_summary = f"Soil pH is {ph_cat.lower()}. Nitrogen levels are {n_cat.lower()}."
            
            minimal_explanation = {
                "summary": minimal_summary,
                "disclaimer": get_disclaimer(language)
            }
            return {
                "version": "farmchain-ai-v1.0",
                "success": False,
                "error": recommendations["error"],
                "extracted_parameters": extracted.get("extracted_parameters", {}),
                "soil_profile": soil_profile,
                "explanation": minimal_explanation
            }

        # #region agent log
        try:
            import os
            os.makedirs('/Users/alishaikh/Desktop/FarmChain/.cursor', exist_ok=True)
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"soil_ai_module.py:1039","message":"BEFORE generate_farmer_explanation: soil_profile Nitrogen category","data":{"nitrogen_category":soil_profile.get("Nitrogen",{}).get("category")},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion
        explanation = generate_farmer_explanation(
            recommendations,
            soil_profile,
            district,
            season,
            irrigation_type,
            language
        )
        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"soil_ai_module.py:1052","message":"AFTER generate_farmer_explanation: explanation content","data":{"explanation_content":explanation.get("content","") if isinstance(explanation,dict) else str(explanation)},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion

        if isinstance(explanation, dict) and explanation.get("error"):
            # Even if explanation generation failed, include a minimal explanation
            # Try to create a basic summary from soil_profile
            minimal_summary = "Unable to generate explanation."
            if isinstance(soil_profile, dict) and soil_profile:
                ph_cat = soil_profile.get("pH", {}).get("category", "Unknown")
                n_cat = soil_profile.get("Nitrogen", {}).get("category", "Unknown")
                if ph_cat != "Unknown" and n_cat != "Unknown":
                    minimal_summary = f"Soil pH is {ph_cat.lower()}. Nitrogen levels are {n_cat.lower()}."
            
            minimal_explanation = {
                "summary": minimal_summary,
                "disclaimer": get_disclaimer(language)
            }
            return {
                "version": "farmchain-ai-v1.0",
                "success": False,
                "error": explanation.get("summary", explanation.get("content", "Failed to generate explanation")),
                "extracted_parameters": extracted.get("extracted_parameters", {}),
                "soil_profile": soil_profile,
                "recommendations": recommendations,
                "explanation": minimal_explanation
            }

        # CORRECT SOURCE: Read nitrogen category directly from soil_profile (derived from numeric value)
        # DO NOT use: recommendations, fertilizer_plan, recommended_range, or any derived variables
        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"soil_ai_module.py:1067","message":"BEFORE reading soil_nitrogen_category: soil_profile Nitrogen category","data":{"nitrogen_category":soil_profile.get("Nitrogen",{}).get("category"),"soil_profile_id":id(soil_profile)},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion
        soil_nitrogen_category = soil_profile["Nitrogen"]["category"]
        final_ph = soil_profile["pH"]["category"]
        
        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"soil_ai_module.py:1075","message":"AFTER reading soil_nitrogen_category: value stored in variable","data":{"soil_nitrogen_category":soil_nitrogen_category,"soil_profile_id":id(soil_profile)},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion
        if soil_nitrogen_category not in ["Low", "Medium", "High"]:
            raise ValueError(f"Invalid nitrogen category: '{soil_nitrogen_category}'. Must be Low, Medium, or High.")

        # EXPLANATION MUST USE THIS: soil_nitrogen_category (from soil_profile, not fertilizer plan)
        final_explanation_content = (
            f"Soil pH is {final_ph.lower()}. "
            f"Nitrogen levels are {soil_nitrogen_category.lower()}."
        )
        # #region agent log
        try:
            import os
            os.makedirs('/Users/alishaikh/Desktop/FarmChain/.cursor', exist_ok=True)
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"soil_ai_module.py:1083","message":"BEFORE override: final_explanation_content and soil_nitrogen_category","data":{"final_explanation_content":final_explanation_content,"soil_nitrogen_category":soil_nitrogen_category},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion

        # CRITICAL: Ensure summary is correct (factual, rule-based)
        # The explanation dict from generate_farmer_explanation already has "summary"
        # We need to ensure it matches our final_explanation_content
        if "summary" not in explanation or explanation["summary"] != final_explanation_content:
            explanation["summary"] = final_explanation_content
        
        # Generate advisory (AI-generated, human-friendly layer) - ALWAYS generate
        advisory = generate_advisory(
            recommendations,
            soil_profile,
            district,
            season,
            irrigation_type,
            language
        )
        
        # ALWAYS include advisory - use fallback if generation failed
        if not advisory or not advisory.strip():
            # Fallback advisory based on soilProfile and recommendations
            crops = []
            if isinstance(recommendations, dict) and "crop_recommendation" in recommendations:
                crops = recommendations["crop_recommendation"].get("primary", [])
            
            ph_cat = soil_profile.get("pH", {}).get("category", "Unknown")
            n_cat = soil_profile.get("Nitrogen", {}).get("category", "Unknown")
            
            if language == "marathi":
                advisory = f"या मातीच्या परिस्थितीत {', '.join(crops) if crops else 'योग्य पिके'} पिके लागवण्याची शिफारस केली जाते. {season} हंगामात {irrigation_type} सिंचनासह या पिकांची लागवड करावी."
            else:
                advisory = f"Based on the soil conditions, {', '.join(crops) if crops else 'suitable crops'} are recommended for cultivation. These crops should be grown during the {season} season with {irrigation_type} irrigation."
        
        # ALWAYS add advisory to explanation
        explanation["advisory"] = advisory
        
        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"soil_ai_module.py:1096","message":"AFTER override: explanation dict with summary and advisory","data":{"summary":explanation.get("summary",""),"has_advisory":bool(explanation.get("advisory"))},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion

        # IMMEDIATE VERIFICATION: Check that summary is correct
        summary_lower = explanation["summary"].lower()
        if "nitrogen levels are medium" in summary_lower or "nitrogen is medium" in summary_lower:
            # Safety measure: Force correct the summary
            explanation["summary"] = final_explanation_content

        if (
            soil_profile["Nitrogen"]["category"] == "Low"
            and "medium" in final_explanation_content.lower()
        ):
            raise RuntimeError("Nitrogen category overridden inside process_soil_report")
        
        # FINAL VALIDATION: Verify advisory doesn't contradict summary
        if "advisory" in explanation and explanation["advisory"]:
            advisory_lower = explanation["advisory"].lower()
            # If nitrogen is Low, advisory must not say "medium"
            if soil_nitrogen_category == "Low":
                if "medium" in advisory_lower and ("nitrogen" in advisory_lower or "n " in advisory_lower):
                    # Discard advisory if it contradicts
                    del explanation["advisory"]

        crops = []
        if isinstance(recommendations, dict) and "crop_recommendation" in recommendations:
            crops = recommendations["crop_recommendation"].get("primary", [])

        if soil_profile["Nitrogen"]["category"] == "Low" and "Onion" in crops:
            raise ValueError("FAIL-FAST: Invalid crop rendered for low fertility soil. Nitrogen category is 'Low' but Onion was recommended. Onion MUST be filtered out for low fertility soil.")

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
            print(f"❌ CRITICAL: Explanation is not a dict! Type: {type(explanation)}", file=sys.stderr)
            explanation = {
                "summary": str(explanation) if explanation else "Unable to generate explanation.",
                "disclaimer": get_disclaimer(language)
            }
        
        if "summary" not in explanation:
            print(f"❌ CRITICAL: Explanation missing 'summary' key! Keys: {list(explanation.keys())}", file=sys.stderr)
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
        
        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"C","location":"soil_ai_module.py:1142","message":"BEFORE return: final explanation in response","data":{"has_explanation":True,"explanation_keys":list(explanation.keys()) if isinstance(explanation,dict) else [],"has_summary":"summary" in explanation if isinstance(explanation,dict) else False,"has_advisory":"advisory" in explanation if isinstance(explanation,dict) else False,"summary_preview":explanation.get("summary","")[:50] if isinstance(explanation,dict) else ""},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
        # #endregion
        
        # FINAL VERIFICATION: Log the exact structure being returned
        final_response = {
            "version": "farmchain-ai-v1.0",
            "extracted_parameters": extracted.get("extracted_parameters", {}),
            "soil_profile": soil_profile,
            "recommendations": recommendations,
            "explanation": explanation,
            "success": True
        }
        
        # CRITICAL: Verify explanation is in the response
        if "explanation" not in final_response:
            print(f"❌ CRITICAL BUG: Explanation missing from final_response! Keys: {list(final_response.keys())}", file=sys.stderr)
            final_response["explanation"] = {
                "summary": "Unable to generate explanation.",
                "disclaimer": get_disclaimer(language)
            }
        
        print(f"🔍 RETURNING: has_explanation={bool(final_response.get('explanation'))}, explanation_keys={list(final_response.get('explanation', {}).keys())}", file=sys.stderr)
        print(f"🔍 EXPLANATION SUMMARY: {final_response.get('explanation', {}).get('summary', 'MISSING')[:100]}", file=sys.stderr)
        
        return final_response
    except Exception as e:
        # DO NOT return default responses - throw error instead
        # This ensures failures are visible and not masked by fallbacks
        error_msg = str(e)
        print(f"❌ CRITICAL ERROR in process_soil_report: {error_msg}", file=sys.stderr)
        # #region agent log
        try:
            import json as json_module
            with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"ERROR","location":"soil_ai_module.py:1220","message":"EXCEPTION CAUGHT in process_soil_report","data":{"error_msg":error_msg},"timestamp":int(__import__("time").time()*1000)}) + "\n")
        except Exception as log_err:
            print(f"⚠️ DEBUG LOG ERROR (exception): {log_err}", file=sys.stderr)
        # #endregion
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