# sAI Global Web Design System & Templates

This reference guide provides high-fidelity CSS and UI/UX design tokens that the agents must follow to build modern, industry-standard web pages.

---

## 🅰️ Premium Typography (Google Fonts)
Always use these Google Fonts combinations by adding the `<link>` tag to `<head>`:
```html
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@300;400;500;700&family=Playfair+Display:ital,wght@0,600;1,400&family=Fira+Code:wght@400;600&display=swap" rel="stylesheet">
```

### Font Pairings:
1. **Modern Premium**: `font-family: 'Outfit', sans-serif;` for Headings, `font-family: 'Inter', sans-serif;` for UI controls & body.
2. **Editorial/Elegant**: `font-family: 'Playfair Display', serif;` for Headings, `font-family: 'Inter', sans-serif;` for body.
3. **Tech/Terminal**: `font-family: 'Fira Code', monospace;` for logs, cards, and input fields.

---

## 🎨 UI/UX Varieties & CSS Boilerplates

### 1. Claymorphic UI Style (Soft, Plastic 3D Look)
```css
:root {
    --bg: #e3edf7;
    --text: #31456a;
    --card: #e3edf7;
    --shadow-dark: #b8c9db;
    --shadow-light: #ffffff;
    --accent: #4589f6;
}
body {
    background-color: var(--bg);
    color: var(--text);
}
.clay-card {
    background: var(--card);
    border-radius: 24px;
    box-shadow: 
        9px 9px 16px var(--shadow-dark), 
        -9px -9px 16px var(--shadow-light);
    border: 1px solid rgba(255, 255, 255, 0.2);
}
.clay-input {
    background: var(--card);
    border: none;
    box-shadow: 
        inset 3px 3px 6px var(--shadow-dark), 
        inset -3px -3px 6px var(--shadow-light);
    border-radius: 14px;
}
.clay-btn {
    box-shadow: 
        3px 3px 6px var(--shadow-dark), 
        -3px -3px 6px var(--shadow-light);
    transition: 0.2s ease;
}
.clay-btn:hover {
    box-shadow: 
        inset 2px 2px 4px var(--shadow-dark), 
        inset -2px -2px 4px var(--shadow-light);
}
```

### 2. Glassmorphic UI Style (Translucent Frosted Glass)
```css
:root {
    --bg-gradient: linear-gradient(135deg, #0f172a, #1e293b);
    --glass-card: rgba(255, 255, 255, 0.05);
    --glass-border: rgba(255, 255, 255, 0.1);
    --glass-shadow: rgba(0, 0, 0, 0.3);
}
body {
    background: var(--bg-gradient);
    min-height: 100vh;
}
.glass-card {
    background: var(--glass-card);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--glass-border);
    border-radius: 20px;
    box-shadow: 0 8px 32px 0 var(--glass-shadow);
}
```

### 3. Neo-Brutalist UI Style (High Contrast Flat Colors)
```css
:root {
    --bg: #f3f4f6;
    --yellow: #fde047;
    --border: #000000;
}
.brutalist-card {
    background-color: #ffffff;
    border: 3px solid var(--border);
    box-shadow: 4px 4px 0 0 var(--border);
    border-radius: 12px;
    transition: 0.15s ease;
}
.brutalist-card:hover {
    transform: translate(-2px, -2px);
    box-shadow: 6px 6px 0 0 var(--border);
}
.brutalist-btn {
    background-color: var(--yellow);
    border: 3px solid var(--border);
    box-shadow: 3px 3px 0 0 var(--border);
    font-weight: 700;
}
```

---

## ⚡ Micro-Animations & Interactive States
* **Smooth Scaling**: `transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);`
* **Pulsing Hover**: Add glow animations for search states.
* **Responsive Layouts**: Always use `display: flex;` or `display: grid;` for page setups, ensuring grid cells align gracefully on narrow devices.
