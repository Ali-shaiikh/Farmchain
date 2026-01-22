#!/usr/bin/env python3
import sys
import json
import os
from soil_ai_module import process_soil_report

# Helper: Convert numeric soil values to categories
def categorize_soil_value(param_name, value):
    if param_name == "pH":
        if value < 6.5:
            return "Low"
        elif value <= 7.5:
            return "Medium"
        else:
            return "High"
    elif param_name == "EC":  # Electrical Conductivity
        if value < 0.75:
            return "Low"
        elif value <= 2.0:
            return "Medium"
        else:
            return "High"
    elif param_name == "Organic Matter":
        if value < 1.0:
            return "Low"
        elif value <= 3.0:
            return "Medium"
        else:
            return "High"
    # Add other parameters as needed
    else:
        return "Unknown"

# Convert numeric values in extracted_parameters to categories
def convert_numeric_to_category(result):
    if "extracted_parameters" in result:
        for param, info in result["extracted_parameters"].items():
            if "value" in info:
                numeric_value = info["value"]
                category = categorize_soil_value(param, numeric_value)
                result["extracted_parameters"][param]["category"] = category
                del result["extracted_parameters"][param]["value"]  # Remove numeric
    return result

if __name__ == "__main__":
    try:
        input_data = json.loads(sys.stdin.read())
        
        result = process_soil_report(
            report_text=input_data.get("report_text", ""),
            district=input_data.get("district", ""),
            soil_type=input_data.get("soil_type"),
            irrigation_type=input_data.get("irrigation_type", "Rain-fed"),
            season=input_data.get("season", "Kharif"),
            language=input_data.get("language", "marathi")
        )

        # Convert numeric values to categories for safety
        result = convert_numeric_to_category(result)
        
        # Ensure explanation exists
        if "explanation" not in result:
            result["explanation"] = {
                "summary": "Unable to generate explanation.",
                "disclaimer": "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
            }

        print(json.dumps(result, ensure_ascii=False))

    except Exception as e:
        error_result = {
            "success": False,
            "error": str(e),
            "explanation": {
                "summary": "An error occurred while processing the soil report.",
                "disclaimer": "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
            }
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)
