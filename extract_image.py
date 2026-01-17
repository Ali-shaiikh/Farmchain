#!/usr/bin/env python3
import sys
import os

try:
    import pytesseract
    from PIL import Image
except ImportError:
    print("Error: Please install pytesseract and Pillow: pip install pytesseract pillow", file=sys.stderr)
    print("Also install Tesseract OCR: https://github.com/tesseract-ocr/tesseract", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 extract_image.py <image_file_path>", file=sys.stderr)
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    try:
        image = Image.open(file_path)
        try:
            text = pytesseract.image_to_string(image, lang='eng+deu')
        except:
            text = pytesseract.image_to_string(image, lang='eng')
        
        print(text)
    except Exception as e:
        print(f"Error extracting text from image: {str(e)}", file=sys.stderr)
        sys.exit(1)
