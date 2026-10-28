# -*- coding: utf-8 -*-
"""
Sentence-level bilingual alignment algorithm for pairing English sentences with Chinese translations.
"""

import re

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def split_en_sentences(text):
    text = clean_text(text)
    abbrevs = [
        ("e.g.", "<E_G>"), ("i.e.", "<I_E>"), ("et al.", "<ET_AL>"),
        ("Fig.", "<FIG>"), ("Table.", "<TABLE>"), ("Eq.", "<EQ>"),
        ("Ref.", "<REF>"), ("vs.", "<VS>"), ("al.", "<AL>"),
        ("no.", "<NO>"), ("Vol.", "<VOL>"), ("pp.", "<PP>"),
        ("approx.", "<APPROX>"), ("min.", "<MIN>"), ("sec.", "<SEC>")
    ]
    for orig, rep in abbrevs:
        text = text.replace(orig, rep)
    
    # Protect decimal numbers (e.g., 0.25)
    text = re.sub(r'(\d+)\.(\d+)', r'\1<DECIMAL>\2', text)
    
    # Split by period, question mark, exclamation mark followed by space or capital
    parts = re.split(r'([.?!]+(?=[\s"\'\)\]]+[A-Z0-9]|$))', text)
    sentences = []
    for i in range(0, len(parts)-1, 2):
        s = (parts[i] + parts[i+1]).strip()
        if s:
            sentences.append(s)
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())
    
    restored = []
    for s in sentences:
        for orig, rep in abbrevs:
            s = s.replace(rep, orig)
        s = s.replace("<DECIMAL>", ".")
        s = clean_text(s)
        if s:
            restored.append(s)
    return restored

def split_zh_sentences(text):
    text = clean_text(text)
    text = re.sub(r'(\d+)\.(\d+)', r'\1<DECIMAL>\2', text)
    parts = re.split(r'([。！？]+)', text)
    sentences = []
    for i in range(0, len(parts)-1, 2):
        s = (parts[i] + parts[i+1]).strip()
        if s:
            sentences.append(s)
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())
    
    restored = [clean_text(s.replace("<DECIMAL>", ".")) for s in sentences if clean_text(s)]
    return restored

def align_sentences(en_list, zh_list):
    if len(en_list) == len(zh_list):
        return list(zip(en_list, zh_list))
    
    n_en, n_zh = len(en_list), len(zh_list)
    pairs = []
    if n_en < n_zh:
        step = n_zh / n_en
        for i in range(n_en):
            start = int(round(i * step))
            end = int(round((i + 1) * step))
            zh_chunk = " ".join(zh_list[start:end])
            pairs.append((en_list[i], zh_chunk))
    else:
        step = n_en / n_zh
        for i in range(n_zh):
            start = int(round(i * step))
            end = int(round((i + 1) * step))
            en_chunk = " ".join(en_list[start:end])
            pairs.append((en_chunk, zh_list[i]))
    return pairs
