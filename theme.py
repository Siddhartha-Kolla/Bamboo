"""Bamboo's colours and font, shared by the popup and the settings window.

The palette comes from the panda artwork: deep navy surfaces, a cream foreground (the panda's face),
indigo for buttons. Text colours are checked against their backgrounds in test_backend.py
(theme_contrast_meets_wcag), so a future tweak cannot quietly make something unreadable.
"""
BG, CARD, FIELD, BORDER = "#1b2444", "#202947", "#2b3660", "#3a4676"      # window, card, input, outline
FG, DIM = "#f5f2e9", "#a3acd1"                                              # main text, secondary text
ACCENT = "#a9adff"                                                          # links, highlights, accent bars (text-safe indigo)
ACCENT_FILL = "#625cff"                                                     # solid buttons; text on it is white
OK, WARN, ERR = "#8fd9b6", "#f2b866", "#ff8a94"                             # mint, amber, coral
ON_FILL = "#ffffff"
FONT = "Segoe UI"       # the popup upgrades this to "Segoe UI Variable Text" when that font is installed
