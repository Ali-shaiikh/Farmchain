#!/usr/bin/env python3
import sys
import json
import os
from soil_ai_module import process_soil_report

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
        
        # CRITICAL VERIFICATION: Ensure explanation exists before output
        if "explanation" not in result:
            print(f"❌ CRITICAL: Explanation missing from result! Keys: {list(result.keys())}", file=sys.stderr)
            result["explanation"] = {
                "summary": "Unable to generate explanation.",
                "disclaimer": "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
            }
        
        # Log the final result structure before output
        print(f"🔍 FINAL RESULT: has_explanation={bool(result.get('explanation'))}, explanation_keys={list(result.get('explanation', {}).keys())}", file=sys.stderr)
        
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        # Ensure explanation is always included even in error cases
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
