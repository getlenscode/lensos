return {
    -- Pinned to the legacy branch: the rewritten `main` branch ignores the
    -- highlight/indent options used below.
    'nvim-treesitter/nvim-treesitter',
    branch = 'master',
    lazy = false,
    build = ':TSUpdate',
    config = function()
	require("nvim-treesitter").setup({
	    highlight = {
		enable = true,
		additional_vim_regex_highlights = false,
	    },
	    indent = { enable = true },
	    autotage = { enable = true },
	    ensure_installed = {
		"lua",
		"tsx",
		"typescript",
		"python",
		"javascript",
		"markdown",
		"markdown_inline",
		"vim",
		"vimdoc",
		"query",
	    },
	    auto_install = true,
	})

    end

}
