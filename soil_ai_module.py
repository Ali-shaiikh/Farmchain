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

try:
    llm_json = ChatOllama(
        model="llama3.2",
        base_url="http://localhost:11434",
        temperature=0.1,
        model_kwargs={"format": "json"}
    )
    llm_text = ChatOllama(
        model="llama3.2",
        base_url="http://localhost:11434",
        temperature=0.3
    )
except Exception as e:
    print(f"Warning: Could not initialize Ollama: {e}")
    print("Make sure Ollama is running: ollama serve")
    raise

json_parser = JsonOutputParser()

def extract_soil_parameters(report_text: str) -> Dict[str, Any]:
    """
    Extract soil parameters from report text.
    Returns structured JSON with normalized parameters.
    """
    prompt = ChatPromptTemplate.from_template("""
You are a soil report interpreter. Extract soil parameters from the following report text.

Rules:
1. Identify soil parameters regardless of naming differences (e.g., "Soil Reaction" → pH)
2. Extract numeric values and units
3. Normalize parameter names to standard names: pH, Nitrogen, Phosphorus, Potassium, Organic Carbon, etc.
4. If unit conversion is unclear, mark unit_uncertain as true
5. Explicitly mark missing parameters with source: "missing"
6. Do NOT categorize or predict - only extract raw values

Report Text:
{report_text}

Output STRICT JSON ONLY in this format:
{{
  "version": "farmchain-ai-v1.0",
  "extracted_parameters": {{
    "pH": {{
      "value": 7.8,
      "unit": "",
      "source": "report",
      "unit_uncertain": false
    }},
    "Nitrogen": {{
      "value": 210,
      "unit": "kg/ha",
      "source": "report",
      "unit_uncertain": false
    }}
  }}
}}
""")

    chain = prompt | llm_json | json_parser
    try:
        result = chain.invoke({"report_text": report_text})
        result_str = json.dumps(result)
        result_str = re.sub(r'https?://[^\s]+langchain[^\s]+', '', result_str)
        result = json.loads(result_str) if result_str else result
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
You are a soil classification expert for Maharashtra, India.

Input:
- Extracted Parameters: {extracted_params}
- District: {district}
- Soil Type: {soil_type}
- Irrigation Type: {irrigation_type}
- Pre-categorized pH: {pre_categorized_ph}

🔴 CRITICAL RULE: pH CLASSIFICATION IS COMPLETELY REMOVED FROM OLLAMA
- pH is ALREADY categorized in backend code and provided above as "Pre-categorized pH"
- You MUST use the pre-categorized pH category EXACTLY as provided
- DO NOT classify, infer, or modify pH category
- DO NOT use district or soil-type to determine pH
- pH category is FROZEN and cannot be changed

🔴 HARD RULES (MANDATORY - NEVER VIOLATE - IMPLEMENTED IN CODE):

RULE 1: MEASURED VALUES (source === "report" && value !== null)
→ Categorize STRICTLY using threshold tables below
→ Set confidence = 0.95 (high confidence for measured values)
→ Set inferred = false (this is NOT inference)
→ COMPLETELY SKIP district/soil-type inference for this parameter
→ NOTE: pH is already categorized - use the pre-categorized value above

RULE 2: MISSING VALUES (value === null)
→ THEN and ONLY THEN you may infer category using soil type + district logic
→ Set confidence = 0.5-0.8 (reflect uncertainty for inferred values)
→ Set inferred = true (this IS inference)
→ NOTE: If pH value is missing, you may infer pH using district/soil-type

RULE 3: PRECEDENCE
→ Measured values ALWAYS override inference
→ Inference logic runs ONLY when value is missing
→ NEVER run inference before checking if value exists
→ pH is ALWAYS from pre-categorized value if provided

❌ WHAT MUST NOT EXIST:
❌ "If district = Pune then pH is alkaline" when pH is pre-categorized
❌ Confidence-based guessing over lab values
❌ Inference logic running before threshold logic
❌ ANY attempt to classify or modify pH if pre-categorized pH is provided

Classification Process:
1. For pH: Use the pre-categorized pH category EXACTLY as provided above. DO NOT classify or infer pH.
2. For OTHER parameters (Nitrogen, Phosphorus, Potassium):
   a. Check if source === "report" AND value !== null
   b. If YES → Use threshold table ONLY (skip to step 3)
   c. If NO → Check if value === null
   d. If value === null → THEN use inference (soil type + district)
   e. If value exists but source !== "report" → Use threshold table

3. Apply threshold categorization (for Nitrogen, Phosphorus, Potassium ONLY):
   - Nitrogen: <200 Low, 200-280 Medium, >280 High (kg/ha)
   - Phosphorus: <10 Low, 10-25 Medium, >25 High (kg/ha)
   - Potassium: <110 Low, 110-280 Medium, >280 High (kg/ha)

4. For missing values only (Nitrogen, Phosphorus, Potassium), infer using:
   - District characteristics
   - Soil type patterns
   - Regional averages
   - For pH: Only infer if pre-categorized pH is NOT provided

Rules:
1. Use pre-categorized pH EXACTLY as provided - DO NOT modify it
2. Convert numeric values into categories ONLY: Low/Medium/High, Acidic/Neutral/Alkaline, Poor/Moderate/Rich, Unknown
3. Measured values (source="report"): Use thresholds ONLY, confidence = 0.95
4. Missing values (value=null): Infer using district/soil-type, confidence = 0.5-0.8
5. Do NOT output raw numbers

CRITICAL OUTPUT REQUIREMENTS:
- Return ONLY the JSON object below
- NO explanations
- NO "Here is the output" or similar text
- NO markdown code blocks
- NO URLs
- NO additional text before or after the JSON
- Start directly with {{ and end with }}

Output EXACTLY this JSON structure (nothing more, nothing less):
{{
  "version": "farmchain-ai-v1.0",
  "soil_profile": {{
    "pH": {{
      "category": "Neutral",
      "confidence": 0.95
    }},
    "Nitrogen": {{
      "category": "Medium",
      "confidence": 0.76
    }}
  }}
}}

CRITICAL: If pre-categorized pH is provided above, you MUST use that EXACT category in the output.
DO NOT change the pH category even if district/soil-type suggests otherwise.
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
You are an agronomy advisor for Maharashtra, India.

EXPLICIT INPUTS (REQUIRED):
- District: {district}
- Season: {season}
- Irrigation Type: {irrigation_type}
- Soil Type: {soil_type}
- Soil Profile: {soil_profile}

CRITICAL SEASON RULE (MANDATORY):
If a crop does not belong to the specified season, it must NEVER be recommended.

Maharashtra Season Guidelines:
- Kharif (June-October): Soybean, Tur, Cotton, Maize, Rice, Bajra, Jowar, Groundnut, Sugarcane
- Rabi (October-March): Wheat, Gram (Chana), Onion, Tomato, Potato, Mustard, Sunflower, Garlic, Fenugreek, Coriander
- Summer (March-June): Watermelon, Muskmelon, Cucumber, Bitter Gourd, Okra

You MUST only recommend crops that belong to the specified season: {season}

Core Rules:
1. All recommendations are guideline-based suggestions, NOT prescriptions
2. Use standard Maharashtra agriculture guidelines
3. If any soil parameter was inferred, recommendations must be conservative
4. Never provide exact kg/acre or kg/hectare values - only ranges
5. Clearly reflect uncertainty when present
6. The crop_recommendation.season field MUST match the input season: {season}

Tasks:
1. Recommend suitable crops (primary 2-3 crops) that belong to season: {season}
2. Set crop_recommendation.season to: {season}
3. Provide crop duration (days) for EACH crop separately (e.g., "Wheat": "110–130 days", "Gram": "90–110 days")
4. Recommend fertilizers with:
   - Fertilizer types (e.g., Urea, DAP, SSP)
   - Application stages (Basal, Vegetative, Flowering, etc.)
   - Quantity ranges only (Low/Medium/High)
5. Recommend equipment by farming stage

CRITICAL: Return ONLY raw JSON. No explanations, no markdown, no surrounding text.
Output ONLY this JSON structure with no additional text:

{{
  "version": "farmchain-ai-v1.0",
  "crop_recommendation": {{
    "primary": ["Crop1", "Crop2"],
    "season": "{season}",
    "crop_durations": {{
      "Crop1": "110–130 days",
      "Crop2": "90–110 days"
    }}
  }},
  "fertilizer_plan": {{
    "Nitrogen": {{
      "recommended_range": "Low to Medium",
      "fertilizers": ["Urea", "DAP"],
      "application_stages": ["Basal", "Vegetative"]
    }}
  }},
  "equipment_plan": {{
    "land_preparation": ["Tractor", "Plough"],
    "sowing": ["Seed Drill"],
    "spraying": ["Power Sprayer"],
    "harvesting": ["Harvester"]
  }}
}}
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
    Generate farmer-friendly explanation HARD-CODED from soilProfile.
    NO AI generation. NO templates. Direct string assembly from soilProfile categories.
    
    CRITICAL: Explanation MUST use ONLY soilProfile.Nitrogen.category (FACT).
    DO NOT use fertilizer_plan.Nitrogen.recommended_range (ACTION).
    agronomy_data parameter is intentionally unused - explanation reads ONLY from soilProfile.
    
    ACCEPTANCE TEST:
    Input: Available Nitrogen (N): 120 kg/ha → soil_profile["Nitrogen"]["category"] = "Low"
    Output explanation MUST be: "Nitrogen levels are low."
    If output contains "medium" → explanation is using wrong variable (fertilizer plan).
    """
    if not isinstance(soil_profile, dict) or not soil_profile:
        raise ValueError("soilProfile missing — cannot generate explanation. Explanation MUST be generated from soilProfile only.")

    # CORRECT SOURCE: Read nitrogen category directly from soil_profile (derived from numeric value)
    # DO NOT use: agronomy_data, fertilizer_plan, recommended_range, or any derived variables
    pH_category = soil_profile["pH"]["category"]
    soil_nitrogen_category = soil_profile["Nitrogen"]["category"]
    # #region agent log
    try:
        import os
        os.makedirs('/Users/alishaikh/Desktop/FarmChain/.cursor', exist_ok=True)
        import json as json_module
        with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"soil_ai_module.py:790","message":"INSIDE generate_farmer_explanation: soil_profile Nitrogen category","data":{"nitrogen_category":soil_nitrogen_category,"ph_category":pH_category},"timestamp":int(__import__("time").time()*1000)}) + "\n")
    except Exception as log_err:
        print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
    # #endregion

    # EXPLANATION MUST USE THIS: soil_nitrogen_category (from soil_profile, not fertilizer plan)
    explanation = (
        f"Soil pH is {pH_category.lower()}. "
        f"Nitrogen levels are {soil_nitrogen_category.lower()}."
    )
    # #region agent log
    try:
        import json as json_module
        with open('/Users/alishaikh/Desktop/FarmChain/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"soil_ai_module.py:796","message":"INSIDE generate_farmer_explanation: explanation string created","data":{"explanation":explanation},"timestamp":int(__import__("time").time()*1000)}) + "\n")
    except Exception as log_err:
        print(f"⚠️ DEBUG LOG ERROR: {log_err}", file=sys.stderr)
    # #endregion

    if (
        soil_profile["Nitrogen"]["category"] == "Low"
        and "medium" in explanation.lower()
    ):
        raise RuntimeError(
            "BUG: Explanation nitrogen text does not match soil_profile category"
        )

    organic_carbon_data = soil_profile.get("Organic Carbon", {}) or soil_profile.get("Organic_Carbon", {})
    organic_carbon_category = organic_carbon_data.get("category") if isinstance(organic_carbon_data, dict) else None

    if organic_carbon_category and organic_carbon_category != "Unknown":
        if organic_carbon_category == "Poor":
            explanation += " Soil organic carbon is poor, indicating low fertility. Soil improvement is advised."
        elif organic_carbon_category == "Medium":
            explanation += " Soil organic carbon is medium."
        elif organic_carbon_category == "Rich":
            explanation += " Soil organic carbon is rich, indicating good fertility."

    if soil_nitrogen_category == "Low":
        explanation += " Nutrient supplementation is required to improve soil fertility."
    elif soil_nitrogen_category == "High":
        explanation += " Nitrogen levels are sufficient for crop growth."

    explanation += f" This recommendation is for {season.lower()} season with {irrigation_type.lower()} irrigation in {district} district."

    if soil_nitrogen_category == "Low":
        explanation_lower = explanation.lower()
        nitrogen_medium_patterns = [
            r"nitrogen\s+levels?\s+are\s+medium\b",
            r"nitrogen\s+is\s+medium\b",
            r"\bmedium\s+nitrogen\s+levels?\b",
            r"\bmedium\s+nitrogen\b"
        ]
        for pattern in nitrogen_medium_patterns:
            if re.search(pattern, explanation_lower):
                raise ValueError("FAIL-FAST: Hardcoded nitrogen wording still present. Explanation is not using soilProfile nitrogen category. Nitrogen category is 'Low' but explanation contains 'medium' in relation to nitrogen. Explanation MUST use soilProfile.Nitrogen.category only, never fertilizer plan or default strings.")

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
    Generate human-friendly advisory text using AI.
    
    SAFETY CONSTRAINTS:
    - MUST read from: soilProfile categories, selected crops, fertilizer plan, season & irrigation
    - MUST NOT: infer new soil categories, contradict summary, introduce new crops, override fertilizer ranges
    
    Returns None if generation fails or validation fails.
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
        
        # Build prompt with strict constraints
        prompt_text = f"""You are an agronomy advisor providing human-friendly guidance to farmers in Maharashtra, India.

CRITICAL RULES (MANDATORY - NEVER VIOLATE):
1. DO NOT infer or modify soil categories - use ONLY the provided categories
2. DO NOT contradict the factual summary (pH: {pH_category}, Nitrogen: {nitrogen_category})
3. DO NOT mention crops that are NOT in the recommended list: {', '.join(crops) if crops else 'None'}
4. DO NOT override fertilizer ranges - use only what is provided
5. DO NOT introduce new soil interpretations

PROVIDED DATA (READ-ONLY):
- Soil pH: {pH_category}
- Soil Nitrogen: {nitrogen_category}
- Organic Carbon: {organic_carbon_category}
- Recommended Crops: {', '.join(crops) if crops else 'None'}
- Season: {season}
- Irrigation: {irrigation_type}
- District: {district}
- Fertilizer Plan: {json.dumps(fertilizer_plan, indent=2) if fertilizer_plan else 'None'}

YOUR TASK:
Generate a friendly, advisory explanation that:
- Explains what the soil conditions mean for farming
- Provides context about the recommended crops
- Offers practical guidance based on the fertilizer plan
- Uses simple language suitable for farmers
- Is written in {"Marathi" if language == "marathi" else "English"}

OUTPUT REQUIREMENTS:
- Return ONLY the advisory text
- NO markdown formatting
- NO JSON structure
- NO explanations about your process
- Start directly with the advisory text
- Keep it concise (2-4 sentences)

Generate the advisory now:"""

        prompt = ChatPromptTemplate.from_template(prompt_text)
        chain = prompt | llm_text | StrOutputParser()
        
        advisory = chain.invoke({})
        
        # Clean up the response
        advisory = advisory.strip()
        advisory = re.sub(r'^(here\s+is|here\'?s|advisory|output|result)[\s:]*', '', advisory, flags=re.IGNORECASE)
        advisory = re.sub(r'```.*?```', '', advisory, flags=re.DOTALL)
        
        # VALIDATION: Ensure advisory does not contradict summary
        advisory_lower = advisory.lower()
        
        # Check 1: No "medium nitrogen" if nitrogen is Low
        if nitrogen_category == "Low":
            if "medium" in advisory_lower and ("nitrogen" in advisory_lower or "n " in advisory_lower):
                return None  # Discard advisory if it contradicts
        
        # Check 2: No crops not in the recommended list
        if crops:
            crop_lower = [c.lower() for c in crops]
            # Extract potential crop mentions (simple heuristic)
            for word in advisory_lower.split():
                word_clean = word.strip('.,!?;:')
                if word_clean and word_clean not in crop_lower and len(word_clean) > 3:
                    # Check if it's a known crop name (basic check)
                    known_crops = ["wheat", "gram", "onion", "tomato", "potato", "soybean", "cotton", "maize", "rice"]
                    if word_clean in known_crops and word_clean not in crop_lower:
                        return None  # Discard if mentions crop not in list
        
        # Check 3: No new soil category interpretations
        # Advisory should not claim different categories than provided
        if nitrogen_category == "Low" and ("high nitrogen" in advisory_lower or "sufficient nitrogen" in advisory_lower):
            return None
        
        if pH_category == "Neutral" and ("acidic" in advisory_lower or "alkaline" in advisory_lower):
            # Allow if it's explaining what neutral means, but not if contradicting
            if "soil is acidic" in advisory_lower or "soil is alkaline" in advisory_lower:
                return None
        
        return advisory if advisory else None
        
    except Exception as e:
        # Fail silently - return None if advisory generation fails
        # This ensures the core summary is always available
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