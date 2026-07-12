import os
import uuid
import re
import asyncio
from pathlib import Path
from typing import AsyncGenerator

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR.parent / "static"
OUTPUTS_DIR = STATIC_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Lazy loading flags
HAS_AI_LIBS = False
try:
    import torch
    import transformers
    import diffusers
    HAS_AI_LIBS = True
except ImportError:
    HAS_AI_LIBS = False

# We will maintain singleton instances of the pipelines if loaded
_text_pipeline = None
_image_pipeline = None
_video_pipeline = None

def get_fallback_theme(prompt: str) -> dict:
    p = (prompt or "").lower()
    # Define color schemes and themes based on keywords
    if any(k in p for k in ("sea", "ocean", "wave", "water", "surf", "beach", "blue", "swim")):
        return {
            "primary": "#0077be",
            "secondary": "#00f5d4",
            "bg": "linear-gradient(135deg, #0b1528 0%, #004e89 100%)",
            "type": "ocean",
            "element": "waves"
        }
    elif any(k in p for k in ("banana", "fruit", "yellow", "monkey", "nano")):
        return {
            "primary": "#ffd166",
            "secondary": "#f4a261",
            "bg": "linear-gradient(135deg, #2b1f0c 0%, #6e4e02 100%)",
            "type": "banana",
            "element": "banana"
        }
    elif any(k in p for k in ("space", "star", "galaxy", "planet", "cosmic", "night", "sky", "moon")):
        return {
            "primary": "#8a2be2",
            "secondary": "#00ffff",
            "bg": "linear-gradient(135deg, #020005 0%, #150030 100%)",
            "type": "space",
            "element": "space"
        }
    elif any(k in p for k in ("forest", "tree", "nature", "green", "grass", "garden", "flower")):
        return {
            "primary": "#2a9d8f",
            "secondary": "#e9c46a",
            "bg": "linear-gradient(135deg, #0e1e12 0%, #1c351e 100%)",
            "type": "nature",
            "element": "nature"
        }
    elif any(k in p for k in ("fire", "sunset", "red", "orange", "heat", "warm", "burn", "volcano")):
        return {
            "primary": "#e76f51",
            "secondary": "#f4a261",
            "bg": "linear-gradient(135deg, #240c08 0%, #5c1809 100%)",
            "type": "fire",
            "element": "fire"
        }
    else:
        # Default Aurora Theme (clay/aurora gradient)
        return {
            "primary": "#cc5a37",
            "secondary": "#e08560",
            "bg": "linear-gradient(135deg, #120e16 0%, #291a27 100%)",
            "type": "aurora",
            "element": "abstract"
        }

def make_svg_image(prompt: str, theme: dict) -> str:
    primary = theme["primary"]
    secondary = theme["secondary"]
    bg = theme["bg"]
    elem_type = theme["element"]
    
    # Base SVG header
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="100%" height="100%">
    <defs>
        <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="{primary}" stop-opacity="0.15" />
            <stop offset="100%" stop-color="{secondary}" stop-opacity="0.05" />
        </linearGradient>
        <radialGradient id="glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="{secondary}" stop-opacity="0.8" />
            <stop offset="100%" stop-color="{primary}" stop-opacity="0" />
        </radialGradient>
        <style>
            .background {{ fill: #11131a; }}
            .overlay {{ fill: url(#bgGrad); }}
            .text {{ font-family: 'Georgia', serif; fill: #f3efe6; font-size: 24px; font-weight: bold; letter-spacing: 0.05em; }}
            .subtext {{ font-family: 'Inter', sans-serif; fill: #a0a5b5; font-size: 14px; letter-spacing: 0.02em; }}
            .badge {{ font-family: monospace; fill: #ffd166; font-size: 11px; }}
            .glow-circle {{ fill: url(#glow); opacity: 0.6; }}
            
            /* Animations */
            @keyframes float {{
                0% {{ transform: translateY(0px) rotate(0deg); }}
                50% {{ transform: translateY(-15px) rotate(3deg); }}
                100% {{ transform: translateY(0px) rotate(0deg); }}
            }}
            @keyframes pulse {{
                0%, 100% {{ transform: scale(1); opacity: 0.4; }}
                50% {{ transform: scale(1.05); opacity: 0.7; }}
            }}
            @keyframes sway {{
                0%, 100% {{ transform: rotate(-5deg); }}
                50% {{ transform: rotate(5deg); }}
            }}
            .animated-element {{
                transform-origin: center;
                animation: float 6s ease-in-out infinite;
            }}
            .pulse-glow {{
                transform-origin: center;
                animation: pulse 4s ease-in-out infinite;
            }}
            .sway-element {{
                transform-origin: bottom center;
                animation: sway 8s ease-in-out infinite;
            }}
        </style>
    </defs>
    <rect class="background" width="800" height="600" rx="20" />
    <rect class="overlay" width="800" height="600" rx="20" />
    """
    
    # Add design depending on theme element
    if elem_type == "waves":
        svg += f"""
        <!-- Ocean Waves design -->
        <g class="pulse-glow" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <g class="animated-element" transform="translate(400, 260)">
            <!-- Sun -->
            <circle cx="0" cy="0" r="70" fill="#ffd166" filter="blur(2px)" />
            <circle cx="0" cy="0" r="55" fill="#f4a261" />
        </g>
        <!-- Sea waves -->
        <path d="M 0,450 C 150,420 250,480 400,450 C 550,420 650,480 800,450 L 800,600 L 0,600 Z" fill="{primary}" opacity="0.8" />
        <path d="M 0,480 C 120,460 280,510 420,480 C 560,450 680,510 800,480 L 800,600 L 0,600 Z" fill="{secondary}" opacity="0.6" />
        <path d="M 0,520 C 180,500 220,540 380,520 C 540,500 620,540 800,520 L 800,600 L 0,600 Z" fill="#0b132b" />
        """
    elif elem_type == "banana":
        svg += f"""
        <!-- Banana design -->
        <g class="pulse-glow" transform="translate(400, 300)">
            <circle class="glow-circle" r="250" cx="0" cy="0" />
        </g>
        <g class="animated-element" transform="translate(400, 260)">
            <!-- Banana shape -->
            <path d="M -80,-50 C -40,-120 80,-120 120,-30 C 130,-10 110,0 90,-20 C 60,-60 -30,-60 -70,-20 C -90,-10 -100,-30 -80,-50 Z" fill="#ffd166" stroke="#f4a261" stroke-width="4" />
            <!-- Stem -->
            <path d="M -80,-50 C -90,-55 -95,-45 -90,-40 C -85,-35 -80,-45 -80,-50 Z" fill="#6e4e02" />
            <circle cx="95" cy="-25" r="4" fill="#6e4e02" />
        </g>
        <!-- Floating bubbles -->
        <circle cx="200" cy="400" r="8" fill="#ffd166" opacity="0.4" class="animated-element" />
        <circle cx="620" cy="180" r="14" fill="#ffd166" opacity="0.2" class="animated-element" />
        <circle cx="580" cy="450" r="6" fill="#f4a261" opacity="0.5" class="animated-element" />
        """
    elif elem_type == "space":
        svg += f"""
        <!-- Space design -->
        <!-- Stars background -->
        <g opacity="0.6">
            <circle cx="120" cy="80" r="1.5" fill="#fff" />
            <circle cx="680" cy="140" r="1.2" fill="#fff" />
            <circle cx="250" cy="480" r="2" fill="#ffd166" />
            <circle cx="580" cy="420" r="1" fill="#fff" />
            <circle cx="190" cy="320" r="1.5" fill="#fff" />
            <circle cx="620" cy="280" r="2.5" fill="#00ffff" />
            <circle cx="340" cy="120" r="1.3" fill="#fff" />
        </g>
        <g class="pulse-glow" transform="translate(400, 250)">
            <circle class="glow-circle" r="260" cx="0" cy="0" />
        </g>
        <g class="animated-element" transform="translate(400, 250)">
            <!-- Giant Planet -->
            <circle cx="0" cy="0" r="85" fill="#1d1135" stroke="{secondary}" stroke-width="3" />
            <!-- Planet Rings -->
            <ellipse cx="0" cy="0" rx="160" ry="32" fill="none" stroke="{primary}" stroke-width="12" opacity="0.8" transform="rotate(-15)" />
            <ellipse cx="0" cy="0" rx="140" ry="24" fill="none" stroke="#fff" stroke-width="2" opacity="0.6" transform="rotate(-15)" />
            <!-- Planet surface shadow -->
            <path d="M 0,-85 A 85,85 0 0,1 85,0 A 85,85 0 0,1 0,85 A 85,85 0 0,1 -85,0 A 85,85 0 0,1 0,-85 Z" fill="none" />
            <path d="M 0,-85 A 85,85 0 0,1 85,0 A 85,85 0 0,1 0,85 Z" fill="#000" opacity="0.3" />
        </g>
        """
    elif elem_type == "nature":
        svg += f"""
        <!-- Nature / Forest design -->
        <g class="pulse-glow" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <!-- Ground hills -->
        <ellipse cx="200" cy="620" rx="400" ry="180" fill="{primary}" opacity="0.9" />
        <ellipse cx="600" cy="640" rx="350" ry="200" fill="{secondary}" opacity="0.7" />
        
        <!-- Swaying trees -->
        <g class="sway-element" transform="translate(250, 480)">
            <!-- Trunk -->
            <rect x="-8" y="-60" width="16" height="60" fill="#4a3728" />
            <!-- Foliage -->
            <circle cx="0" cy="-75" r="45" fill="{primary}" />
            <circle cx="-25" cy="-65" r="30" fill="{secondary}" opacity="0.8" />
            <circle cx="20" cy="-60" r="35" fill="{primary}" />
        </g>
        <g class="sway-element" transform="translate(520, 500)" style="animation-delay: -2s;">
            <!-- Trunk -->
            <rect x="-6" y="-50" width="12" height="50" fill="#4a3728" />
            <!-- Foliage -->
            <circle cx="0" cy="-60" r="38" fill="{secondary}" />
            <circle cx="-15" cy="-55" r="28" fill="{primary}" />
        </g>
        """
    elif elem_type == "fire":
        svg += f"""
        <!-- Fire / Sunset design -->
        <g class="pulse-glow" transform="translate(400, 300)">
            <circle class="glow-circle" r="270" cx="0" cy="0" />
        </g>
        <g class="animated-element" transform="translate(400, 240)">
            <!-- Fire Flame / Sun -->
            <path d="M 0,-80 C 40,-30 80,20 80,80 C 80,124 44,160 0,160 C -44,160 -80,124 -80,80 C -80,20 -40,-30 0,-80 Z" fill="#ffd166" />
            <path d="M 0,-40 C 25,-10 50,20 50,60 C 50,90 27,110 0,110 C -27,110 -50,90 -50,60 C -50,20 -25,-10 0,-40 Z" fill="#e76f51" />
            <path d="M 0,0 C 15,15 25,35 25,50 C 25,65 13,80 0,80 C -13,80 -25,65 -25,50 C -25,35 -15,15 0,0 Z" fill="#f4a261" />
        </g>
        <!-- Rocky horizon -->
        <polygon points="0,520 180,480 320,530 480,470 650,540 800,500 800,600 0,600" fill="#180705" />
        """
    else:
        # Abstract / Aurora design
        svg += f"""
        <!-- Abstract design -->
        <g class="pulse-glow" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <g class="animated-element" transform="translate(400, 260)">
            <!-- Glowing abstract shape -->
            <rect x="-80" y="-80" width="160" height="160" rx="40" fill="none" stroke="{secondary}" stroke-width="5" transform="rotate(45)" opacity="0.8" />
            <rect x="-65" y="-65" width="130" height="130" rx="30" fill="none" stroke="#fff" stroke-width="2" transform="rotate(20)" opacity="0.6" />
            <circle cx="0" cy="0" r="35" fill="{primary}" />
        </g>
        <circle cx="250" cy="200" r="6" fill="#fff" opacity="0.6" class="animated-element" style="animation-delay:-1s" />
        <circle cx="550" cy="380" r="10" fill="{secondary}" opacity="0.4" class="animated-element" style="animation-delay:-3s" />
        """
        
    # Text block
    escaped_prompt = re.sub(r'[\'\"<>]', '', prompt)[:50]
    svg += f"""
    <!-- Metadata Text -->
    <text class="text" x="50" y="80">NANO BANANA</text>
    <text class="subtext" x="50" y="115">"{escaped_prompt}..."</text>
    <text class="badge" x="50" y="540">LOCAL OFFLINE ENGINE (CPU-MOCK)</text>
    <text class="badge" x="50" y="560">To run real models: pip install torch diffusers</text>
</svg>
"""
    return svg

def make_svg_video(prompt: str, theme: dict) -> str:
    primary = theme["primary"]
    secondary = theme["secondary"]
    elem_type = theme["element"]
    
    # Base SVG header
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="100%" height="100%">
    <defs>
        <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="{primary}" stop-opacity="0.2" />
            <stop offset="100%" stop-color="{secondary}" stop-opacity="0.05" />
        </linearGradient>
        <radialGradient id="glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="{secondary}" stop-opacity="0.8" />
            <stop offset="100%" stop-color="{primary}" stop-opacity="0" />
        </radialGradient>
        <style>
            .background {{ fill: #0b0c10; }}
            .overlay {{ fill: url(#bgGrad); }}
            .text {{ font-family: 'Georgia', serif; fill: #ffd166; font-size: 24px; font-weight: bold; letter-spacing: 0.08em; }}
            .subtext {{ font-family: 'Inter', sans-serif; fill: #a0a5b5; font-size: 14px; }}
            .badge {{ font-family: monospace; fill: #a0a5b5; font-size: 11px; }}
            .glow-circle {{ fill: url(#glow); opacity: 0.6; }}
            
            /* High-fidelity CSS Animations for Video Mocking */
            @keyframes panCamera {{
                0% {{ transform: translate(0px, 0px) scale(1); }}
                50% {{ transform: translate(-10px, -5px) scale(1.03); }}
                100% {{ transform: translate(0px, 0px) scale(1); }}
            }}
            
            @keyframes animateWaves {{
                0% {{ d: path("M 0,450 C 150,420 250,480 400,450 C 550,420 650,480 800,450 L 800,600 L 0,600 Z"); }}
                50% {{ d: path("M 0,470 C 180,480 220,420 400,470 C 580,520 620,420 800,470 L 800,600 L 0,600 Z"); }}
                100% {{ d: path("M 0,450 C 150,420 250,480 400,450 C 550,420 650,480 800,450 L 800,600 L 0,600 Z"); }}
            }}
            @keyframes animateWaves2 {{
                0% {{ d: path("M 0,480 C 120,460 280,510 420,480 C 560,450 680,510 800,480 L 800,600 L 0,600 Z"); }}
                50% {{ d: path("M 0,460 C 150,510 250,440 420,460 C 590,480 650,530 800,460 L 800,600 L 0,600 Z"); }}
                100% {{ d: path("M 0,480 C 120,460 280,510 420,480 C 560,450 680,510 800,480 L 800,600 L 0,600 Z"); }}
            }}
            
            @keyframes bounceBanana {{
                0%, 100% {{ transform: translate(400px, 260px) rotate(0deg); }}
                33% {{ transform: translate(410px, 240px) rotate(5deg); }}
                66% {{ transform: translate(390px, 250px) rotate(-5deg); }}
            }}
            
            @keyframes rotationRing {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}
            
            @keyframes cosmicPulse {{
                0%, 100% {{ filter: hue-rotate(0deg) scale(1); }}
                50% {{ filter: hue-rotate(45deg) scale(1.02); }}
            }}
            
            @keyframes swayForest {{
                0%, 100% {{ transform: skewX(-3deg); }}
                50% {{ transform: skewX(3deg); }}
            }}
            
            @keyframes flickerFlame {{
                0%, 100% {{ transform: translate(400px, 240px) scale(1); opacity: 1; }}
                25% {{ transform: translate(398px, 243px) scale(0.98); opacity: 0.95; }}
                50% {{ transform: translate(403px, 237px) scale(1.03); opacity: 1; }}
                75% {{ transform: translate(397px, 241px) scale(0.97); opacity: 0.92; }}
            }}
            
            .camera-box {{
                animation: panCamera 12s ease-in-out infinite;
                transform-origin: center;
            }}
            .animated-wave-1 {{
                animation: animateWaves 5s ease-in-out infinite;
            }}
            .animated-wave-2 {{
                animation: animateWaves2 7s ease-in-out infinite;
            }}
            .dancing-banana {{
                animation: bounceBanana 4s ease-in-out infinite;
                transform-origin: center;
            }}
            .orbit-ring {{
                animation: rotationRing 20s linear infinite;
                transform-origin: 400px 250px;
            }}
            .nebula {{
                animation: cosmicPulse 8s ease-in-out infinite;
                transform-origin: center;
            }}
            .sway-forest {{
                animation: swayForest 6s ease-in-out infinite;
                transform-origin: bottom center;
            }}
            .flame-anim {{
                animation: flickerFlame 0.8s ease-in-out infinite;
                transform-origin: bottom center;
            }}
        </style>
    </defs>
    <rect class="background" width="800" height="600" rx="20" />
    
    <!-- Camera Pan Container -->
    <g class="camera-box">
        <rect class="overlay" width="800" height="600" rx="20" />
    """
    
    # Render based on theme
    if elem_type == "waves":
        svg += f"""
        <g class="nebula" transform="translate(400, 300)">
            <circle class="glow-circle" r="300" cx="0" cy="0" />
        </g>
        <circle cx="400" cy="240" r="60" fill="#ffd166" opacity="0.9" />
        <path class="animated-wave-1" d="M 0,450 C 150,420 250,480 400,450 C 550,420 650,480 800,450 L 800,600 L 0,600 Z" fill="{primary}" opacity="0.8" />
        <path class="animated-wave-2" d="M 0,480 C 120,460 280,510 420,480 C 560,450 680,510 800,480 L 800,600 L 0,600 Z" fill="{secondary}" opacity="0.6" />
        <path d="M 0,530 C 100,520 300,550 450,530 C 600,510 700,550 800,530 L 800,600 L 0,600 Z" fill="#050811" />
        """
    elif elem_type == "banana":
        svg += f"""
        <g class="nebula" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <!-- Floating active background blobs -->
        <circle cx="300" cy="200" r="30" fill="{secondary}" opacity="0.15" />
        <circle cx="500" cy="350" r="45" fill="{primary}" opacity="0.1" />
        
        <g class="dancing-banana">
            <path d="M -80,-50 C -40,-120 80,-120 120,-30 C 130,-10 110,0 90,-20 C 60,-60 -30,-60 -70,-20 C -90,-10 -100,-30 -80,-50 Z" fill="#ffd166" stroke="#f4a261" stroke-width="4" />
            <path d="M -80,-50 C -90,-55 -95,-45 -90,-40 C -85,-35 -80,-45 -80,-50 Z" fill="#6e4e02" />
            <circle cx="95" cy="-25" r="4" fill="#6e4e02" />
        </g>
        """
    elif elem_type == "space":
        svg += f"""
        <!-- Stars and space -->
        <g opacity="0.8">
            <circle cx="100" cy="100" r="1.5" fill="#fff" />
            <circle cx="700" cy="120" r="1.2" fill="#fff" />
            <circle cx="280" cy="450" r="2" fill="#fff" />
            <circle cx="500" cy="400" r="1" fill="#fff" />
            <circle cx="150" cy="300" r="2" fill="#00ffff" />
            <circle cx="650" cy="220" r="2.5" fill="#ffd166" />
        </g>
        <g class="nebula" transform="translate(400, 250)">
            <circle class="glow-circle" r="290" cx="0" cy="0" />
        </g>
        <!-- Orbit ring rotating -->
        <g class="orbit-ring">
            <ellipse cx="400" cy="250" rx="170" ry="35" fill="none" stroke="{primary}" stroke-width="12" opacity="0.6" transform="rotate(-15 400 250)" />
            <ellipse cx="400" cy="250" rx="145" ry="22" fill="none" stroke="#fff" stroke-width="2" opacity="0.5" transform="rotate(-15 400 250)" />
        </g>
        <circle cx="400" cy="250" r="85" fill="#180c2b" stroke="{secondary}" stroke-width="3" />
        <path d="M 400,165 A 85,85 0 0,1 485,250 A 85,85 0 0,1 400,335 Z" fill="#000" opacity="0.25" />
        """
    elif elem_type == "nature":
        svg += f"""
        <g class="nebula" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <ellipse cx="200" cy="620" rx="400" ry="180" fill="{primary}" opacity="0.95" />
        <ellipse cx="600" cy="640" rx="350" ry="200" fill="{secondary}" opacity="0.8" />
        
        <g class="sway-forest" transform="translate(400, 500)">
            <g transform="translate(-150, -10)">
                <rect x="-8" y="-60" width="16" height="60" fill="#4a3728" />
                <circle cx="0" cy="-75" r="45" fill="{primary}" />
            </g>
            <g transform="translate(120, 10)">
                <rect x="-6" y="-50" width="12" height="50" fill="#4a3728" />
                <circle cx="0" cy="-60" r="38" fill="{secondary}" />
            </g>
        </g>
        """
    elif elem_type == "fire":
        svg += f"""
        <g class="nebula" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <g class="flame-anim">
            <path d="M 0,-80 C 40,-30 80,20 80,80 C 80,124 44,160 0,160 C -44,160 -80,124 -80,80 C -80,20 -40,-30 0,-80 Z" fill="#ffd166" />
            <path d="M 0,-40 C 25,-10 50,20 50,60 C 50,90 27,110 0,110 C -27,110 -50,90 -50,60 C -50,20 -25,-10 0,-40 Z" fill="#e76f51" />
            <path d="M 0,0 C 15,15 25,35 25,50 C 25,65 13,80 0,80 C -13,80 -25,65 -25,50 C -25,35 -15,15 0,0 Z" fill="#f4a261" />
        </g>
        <polygon points="0,520 180,480 320,530 480,470 650,540 800,500 800,600 0,600" fill="#140604" />
        """
    else:
        # Abstract
        svg += f"""
        <g class="nebula" transform="translate(400, 300)">
            <circle class="glow-circle" r="280" cx="0" cy="0" />
        </g>
        <g class="dancing-banana">
            <rect x="-80" y="-80" width="160" height="160" rx="45" fill="none" stroke="{secondary}" stroke-width="6" transform="rotate(45)" opacity="0.8" />
            <rect x="-65" y="-65" width="130" height="130" rx="35" fill="none" stroke="#fff" stroke-width="2" transform="rotate(15)" opacity="0.6" />
            <circle cx="0" cy="0" r="35" fill="{primary}" />
        </g>
        """
        
    svg += f"""
    </g> <!-- End Camera Pan -->
    <text class="text" x="50" y="80">SEADANCE VIDEO</text>
    <text class="subtext" x="50" y="115">"{re.sub(r'[\'\"<>]', '', prompt)[:50]}..."</text>
    <text class="badge" x="50" y="540">LOCAL OFFLINE ENGINE (CPU-MOCK)</text>
    <text class="badge" x="50" y="560">To run real models: pip install torch diffusers</text>
</svg>
"""
    return svg

def generate_local_image(prompt: str) -> str:
    filename = f"img_{uuid.uuid4().hex[:8]}.svg"
    out_path = OUTPUTS_DIR / filename
    
    if HAS_AI_LIBS:
        try:
            # Attempt to run real model if imported successfully
            global _image_pipeline
            if _image_pipeline is None:
                # Load tiny/efficient Stable Diffusion pipeline
                # runwayml/stable-diffusion-v1-5 is standard, or we can use a smaller model if needed
                _image_pipeline = diffusers.StableDiffusionPipeline.from_pretrained(
                    "runwayml/stable-diffusion-v1-5",
                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                    safety_checker=None
                )
                if torch.cuda.is_available():
                    _image_pipeline = _image_pipeline.to("cuda")
                    _image_pipeline.enable_attention_slicing()
                else:
                    _image_pipeline = _image_pipeline.to("cpu")
            
            # Generate PNG
            generator = torch.manual_seed(42)
            image = _image_pipeline(prompt, num_inference_steps=15, generator=generator).images[0]
            
            # Save as PNG
            filename_png = f"img_{uuid.uuid4().hex[:8]}.png"
            png_path = OUTPUTS_DIR / filename_png
            image.save(png_path)
            return f"/outputs/{filename_png}"
        except Exception as e:
            # Fall back to SVG if error
            pass
            
    # Fallback to high-quality SVG card
    theme = get_fallback_theme(prompt)
    svg_content = make_svg_image(prompt, theme)
    out_path.write_text(svg_content, encoding="utf-8")
    return f"/outputs/{filename}"

def generate_local_video(prompt: str) -> str:
    # Use SVG animation as standard format (compatible, instant, zero VRAM, lightweight)
    filename = f"vid_{uuid.uuid4().hex[:8]}.svg"
    out_path = OUTPUTS_DIR / filename
    
    if HAS_AI_LIBS:
        try:
            global _video_pipeline
            if _video_pipeline is None:
                # Load efficient damo-vilab text to video pipeline
                _video_pipeline = diffusers.DiffusionPipeline.from_pretrained(
                    "damo-vilab/text-to-video-ms-1.7b",
                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                    variant="fp16" if torch.cuda.is_available() else None
                )
                if torch.cuda.is_available():
                    _video_pipeline = _video_pipeline.to("cuda")
                    _video_pipeline.enable_model_cpu_offload()
                    _video_pipeline.enable_vae_slicing()
                else:
                    _video_pipeline = _video_pipeline.to("cpu")
                    
            # Generate frames
            video_frames = _video_pipeline(prompt, num_frames=8, num_inference_steps=10).frames
            
            # Save as MP4
            filename_mp4 = f"vid_{uuid.uuid4().hex[:8]}.mp4"
            mp4_path = OUTPUTS_DIR / filename_mp4
            diffusers.utils.export_to_video(video_frames, mp4_path.as_posix())
            return f"/outputs/{filename_mp4}"
        except Exception as e:
            pass
            
    # Fallback SVG animated canvas
    theme = get_fallback_theme(prompt)
    svg_content = make_svg_video(prompt, theme)
    out_path.write_text(svg_content, encoding="utf-8")
    return f"/outputs/{filename}"

async def generate_local_text_stream(messages: list[dict], settings: dict) -> AsyncGenerator[str, None]:
    # Extract last prompt
    prompt = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            prompt = m.get("content") or ""
            break
            
    if HAS_AI_LIBS:
        try:
            global _text_pipeline
            if _text_pipeline is None:
                # Load tiny Qwen model (0.5B instruct)
                model_id = "Qwen/Qwen2.5-0.5B-Instruct"
                tokenizer = transformers.AutoTokenizer.from_pretrained(model_id)
                model = transformers.AutoModelForCausalLM.from_pretrained(
                    model_id,
                    torch_dtype=torch.float32, # CPU friendly default
                    device_map="auto"
                )
                _text_pipeline = (model, tokenizer)
            
            model, tokenizer = _text_pipeline
            # Format using chat template
            formatted_input = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = tokenizer([formatted_input], return_tensors="pt").to(model.device)
            
            # Simple streamer to return tokens
            streamer = transformers.TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
            generation_kwargs = dict(inputs, streamer=streamer, max_new_tokens=512, temperature=0.7)
            
            # Run in separate thread so it doesn't block the async event loop
            import threading
            thread = threading.Thread(target=model.generate, kwargs=generation_kwargs)
            thread.start()
            
            for token in streamer:
                yield token
                await asyncio.sleep(0.01)
            return
        except Exception as e:
            # Fall back to mock text
            pass
            
    # CPU Mock text response
    mock_response = (
        f"### Nano Banana Chat (Local CPU-Mock)\n\n"
        f"I received your message: *\"{prompt[:80]}...\"*\n\n"
        f"Currently, I am running in **CPU-Mock Fallback Mode** because the local machine is missing "
        f"the PyTorch/Transformers AI stack, or the packages failed to import.\n\n"
        f"**To activate real local offline generation, run the following command in your terminal:**\n"
        f"```bash\n"
        f"pip install torch transformers\n"
        f"```\n"
        f"Once installed, I will load `Qwen2.5-0.5B-Instruct` completely offline and generate text locally."
    )
    for i in range(0, len(mock_response), 10):
        yield mock_response[i:i+10]
        await asyncio.sleep(0.01)
