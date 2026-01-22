#!/usr/bin/env python3
import sys
import os

try:
    import pdfplumber
except ImportError:
    try:
        import PyPDF2
    except ImportError:
        print("Error: Please install pdfplumber or PyPDF2: pip install pdfplumber", file=sys.stderr)
        sys.exit(1)

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

def extract_text_pdfplumber(file_path):
    """Extract text using pdfplumber"""
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

def extract_text_pypdf2(file_path):
    """Extract text using PyPDF2"""
    text = ""
    with open(file_path, 'rb') as file:
        pdf_reader = PyPDF2.PdfReader(file)
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
    return text

def extract_text_ocr_fallback(file_path):
    """Fallback OCR extraction for PDF pages"""
    if not OCR_AVAILABLE:
        return ""
    
    try:
        import pdf2image
        images = pdf2image.convert_from_path(file_path)
        text = ""
        for img in images:
            try:
                page_text = pytesseract.image_to_string(img, lang='eng')
                if page_text:
                    text += page_text + "\n"
            except Exception as e:
                print(f"⚠️ OCR failed for page: {e}", file=sys.stderr)
        return text
    except Exception as e:
        print(f"⚠️ OCR fallback unavailable: {e}", file=sys.stderr)
        return ""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 extract_pdf.py <pdf_file_path>", file=sys.stderr)
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    try:
        if 'pdfplumber' in sys.modules or 'pdfplumber' in dir():
            text = extract_text_pdfplumber(file_path)
        else:
            text = extract_text_pypdf2(file_path)

        # OCR fallback if extraction is empty or too short
        if len(text.strip()) < 100:
            print("⚠️  WARNING: Extracted text too short or empty. Attempting OCR fallback...", file=sys.stderr)
            ocr_text = extract_text_ocr_fallback(file_path)
            if ocr_text and len(ocr_text.strip()) > 100:
                print("✓ OCR fallback successful. Using OCR-extracted text.", file=sys.stderr)
                text = ocr_text
            else:
                print("✗ OCR fallback failed or returned insufficient text.", file=sys.stderr)
        
        print(text)
    except Exception as e:
        print(f"Error extracting PDF: {str(e)}", file=sys.stderr)
        sys.exit(1)