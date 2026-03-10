import base64
import requests
import time
from config import OLLAMA_MODEL
import ollama
from config import DOCPIPE_API

def extract_text_from_image(image_bytes: bytes) -> str:
    """Extract text from image using Docupipe."""
    url = "https://app.docupipe.ai/document"
    
    encoded_image = base64.b64encode(image_bytes).decode()
    payload = {"document": {"file": {"contents": encoded_image, "filename": "image.jpg"}}}
    headers = {"X-API-Key": DOCPIPE_API}
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        raise Exception("Upload failed")
    
    document_id = response.json()['documentId']
    
    # Wait for processing (faster)
    for i in range(6):
        time.sleep(2)
        doc_info = requests.get(f"https://app.docupipe.ai/document/{document_id}", headers=headers)
        if doc_info.status_code == 200:
            doc_data = doc_info.json()
            if doc_data.get('status') == 'completed':
                return doc_data['result']['text']
    
    raise Exception("Processing timeout")

def process_image_and_answer(raw_text: str) -> str:
    """Single AI call to process image text and answer all questions."""
    prompt = f"""
                You will get task question.

                {raw_text}

                INSTRUKCJE:
                bez komentarzy,
                sam kod,
                przed kodem i na końcu dodaj <code>
                """
    
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response['message']['content'].strip()
    except Exception as e:
        return f"AI Error: {e}"
    