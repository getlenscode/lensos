" peakflava.vim — Deep Purple & Cyan colorscheme for Vim.
" Mirrors the palette in theme/peakflava/COLOR_SCHEME.md.

hi clear
if exists("syntax_on")
  syntax reset
endif
let g:colors_name = "peakflava"
set background=dark

let s:bg        = "#0f0f1e"
let s:bg_alt    = "#1a1a2e"
let s:bg_sel    = "#3b2d5c"
let s:fg        = "#d4d4d8"
let s:fg_dim    = "#71717a"
let s:purple    = "#8b5cf6"
let s:purple_l  = "#a78bfa"
let s:cyan      = "#06b6d4"
let s:cyan_l    = "#22d3ee"
let s:green     = "#10b981"
let s:yellow    = "#f59e0b"
let s:red       = "#ef4444"
let s:blue      = "#3b82f6"

function! s:hl(group, fg, bg, attr) abort
  let l:cmd = "hi " . a:group
  let l:cmd .= " guifg=" . (a:fg == "" ? "NONE" : a:fg)
  let l:cmd .= " guibg=" . (a:bg == "" ? "NONE" : a:bg)
  let l:cmd .= " gui=" . (a:attr == "" ? "NONE" : a:attr)
  let l:cmd .= " cterm=" . (a:attr == "" ? "NONE" : a:attr)
  execute l:cmd
endfunction

call s:hl("Normal",        s:fg,      s:bg,      "")
call s:hl("NormalFloat",   s:fg,      s:bg_alt,  "")
call s:hl("Comment",       s:fg_dim,  "",        "italic")
call s:hl("Constant",      s:purple,  "",        "")
call s:hl("String",        s:green,   "",        "")
call s:hl("Character",     s:green,   "",        "")
call s:hl("Number",        s:yellow,  "",        "")
call s:hl("Boolean",       s:yellow,  "",        "")
call s:hl("Float",         s:yellow,  "",        "")
call s:hl("Identifier",    s:cyan,    "",        "")
call s:hl("Function",      s:cyan_l,  "",        "")
call s:hl("Statement",     s:purple,  "",        "")
call s:hl("Conditional",   s:purple,  "",        "")
call s:hl("Repeat",        s:purple,  "",        "")
call s:hl("Label",         s:purple,  "",        "")
call s:hl("Operator",      s:cyan,    "",        "")
call s:hl("Keyword",       s:purple,  "",        "")
call s:hl("Exception",     s:red,     "",        "")
call s:hl("PreProc",       s:cyan,    "",        "")
call s:hl("Include",       s:cyan,    "",        "")
call s:hl("Define",        s:cyan,    "",        "")
call s:hl("Macro",         s:cyan,    "",        "")
call s:hl("Type",          s:blue,    "",        "")
call s:hl("StorageClass",  s:blue,    "",        "")
call s:hl("Structure",     s:blue,    "",        "")
call s:hl("Special",       s:yellow,  "",        "")
call s:hl("SpecialChar",   s:yellow,  "",        "")
call s:hl("Delimiter",     s:fg,      "",        "")
call s:hl("Underlined",    s:cyan,    "",        "underline")
call s:hl("Error",         s:red,     "",        "bold")
call s:hl("Todo",          s:yellow,  s:bg_alt,  "bold")

call s:hl("Visual",        "",        s:bg_sel,  "")
call s:hl("Search",        s:bg,      s:yellow,  "")
call s:hl("IncSearch",     s:bg,      s:cyan,    "")
call s:hl("CursorLine",    "",        s:bg_alt,  "")
call s:hl("CursorLineNr",  s:purple,  s:bg_alt,  "bold")
call s:hl("LineNr",        s:fg_dim,  "",        "")
call s:hl("SignColumn",    "",        s:bg,      "")
call s:hl("ColorColumn",   "",        s:bg_alt,  "")
call s:hl("VertSplit",     s:bg_sel,  "",        "")
call s:hl("StatusLine",    s:fg,      s:bg_sel,  "")
call s:hl("StatusLineNC",  s:fg_dim,  s:bg_alt,  "")
call s:hl("TabLine",       s:fg_dim,  s:bg_alt,  "")
call s:hl("TabLineSel",    s:bg,      s:purple,  "bold")
call s:hl("TabLineFill",   "",        s:bg,      "")
call s:hl("Pmenu",         s:fg,      s:bg_alt,  "")
call s:hl("PmenuSel",      s:bg,      s:purple,  "")
call s:hl("PmenuSbar",     "",        s:bg_alt,  "")
call s:hl("PmenuThumb",    "",        s:purple,  "")
call s:hl("WildMenu",      s:bg,      s:purple,  "")
call s:hl("MatchParen",    s:cyan,    s:bg_sel,  "bold")
call s:hl("NonText",       s:fg_dim,  "",        "")
call s:hl("SpecialKey",    s:fg_dim,  "",        "")
call s:hl("Whitespace",    s:fg_dim,  "",        "")
call s:hl("Directory",     s:cyan,    "",        "")
call s:hl("Title",         s:purple,  "",        "bold")
call s:hl("Question",      s:green,   "",        "")
call s:hl("MoreMsg",       s:green,   "",        "")
call s:hl("WarningMsg",    s:yellow,  "",        "")
call s:hl("ErrorMsg",      s:red,     "",        "bold")
call s:hl("Folded",        s:fg_dim,  s:bg_alt,  "")
call s:hl("DiffAdd",       "",        "#10352a", "")
call s:hl("DiffChange",    "",        "#1a2a45", "")
call s:hl("DiffDelete",    "",        "#451a24", "")
call s:hl("DiffText",      "",        "#274472", "")
call s:hl("SpellBad",      "",        "",        "undercurl")
call s:hl("CursorColumn",  "",        s:bg_alt,  "")

delfunction s:hl
