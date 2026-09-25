import re

def clean_llm_output(content: str):
    """Remove markdown wrappers like ```json ... ```"""
    
    # Remove ```json and ```
    content = re.sub(r"```json", "", content)
    content = re.sub(r"```", "", content)

    return content.strip()