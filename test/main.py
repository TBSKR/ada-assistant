import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

genai.configure(api_key=api_key)

model = genai.GenerativeModel("gemini-2.5-flash", system_instruction="You are a helpful assistant.")

response = model.generate_content(
    "tell short story?",
    generation_config={},
    stream=True
)

for chunk in response:
    print(chunk.text, end="")