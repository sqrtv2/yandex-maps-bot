#!/usr/bin/env python3
"""Test Gemini persona generation."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from core.ai_persona_generator import generate_personas

p = generate_personas(count=1)
print(f"Generated: {p[0].get('name')}, {p[0].get('city')}, {p[0].get('profession')}")
print(f"Interests: {p[0].get('interests', [])}")
print("OK - Gemini works!")
