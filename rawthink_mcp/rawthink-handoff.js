/**
 * rawthink-handoff.js — SessionStart hook for Claude Code
 *
 * Reads the most recent handoff files from the vault and outputs them
 * to Claude's context. Supports multi-terminal: each session writes its
 * own handoff file, this hook reads the most recent 3 per project.
 *
 * Also lists recent qnotes so Claude can scan for unresolved action items.
 *
 * Usage: node rawthink-handoff.js <VAULT_PATH>
 */
const fs = require('fs');
const path = require('path');

const VAULT_DIR = process.argv[2];
const MAX_HANDOFFS = 3;

if (!VAULT_DIR) {
    console.error('Usage: node rawthink-handoff.js <VAULT_PATH>');
    process.exit(1);
}

if (!fs.existsSync(VAULT_DIR)) {
    console.error(`Vault not found: ${VAULT_DIR}`);
    process.exit(1);
}

let found = false;

// --- Handoff files ---
// Pattern: handoff-{project}-{session_id}.md (new) or handoff-{project}.md (legacy)
const SESSION_RE = /^handoff-(.+)-(\d{4}-\d{2}-\d{2}_\d+)\.md$/;
const LEGACY_RE = /^handoff-(.+)\.md$/;

try {
    const files = fs.readdirSync(VAULT_DIR)
        .filter(f => /^handoff-.*\.md$/.test(f))
        .map(f => {
            const filepath = path.join(VAULT_DIR, f);
            const stat = fs.statSync(filepath);
            // Parse project and session from filename
            let project = null;
            let session = null;
            const sessionMatch = f.match(SESSION_RE);
            if (sessionMatch) {
                project = sessionMatch[1];
                session = sessionMatch[2];
            } else {
                const legacyMatch = f.match(LEGACY_RE);
                if (legacyMatch) {
                    project = legacyMatch[1];
                    session = null; // legacy format
                }
            }
            return { file: f, filepath, project, session, mtime: stat.mtimeMs };
        })
        .filter(f => f.project !== null)
        .sort((a, b) => b.mtime - a.mtime); // newest first

    // Group by project, take most recent MAX_HANDOFFS per project
    const byProject = {};
    for (const f of files) {
        if (!byProject[f.project]) byProject[f.project] = [];
        byProject[f.project].push(f);
    }

    for (const [project, projectFiles] of Object.entries(byProject)) {
        const recent = projectFiles.slice(0, MAX_HANDOFFS);
        const older = projectFiles.length - recent.length;

        for (const f of recent) {
            const content = fs.readFileSync(f.filepath, 'utf-8');
            const sessionLabel = f.session ? f.session : 'latest';
            console.log('=== RAWThink Session Handoff ===');
            console.log(`[project: ${project}]`);
            console.log(content.trim());
            console.log('=== END HANDOFF ===');
            console.log('');
            found = true;
        }

        if (older > 0) {
            console.log(`(${older} older handoffs for ${project} available in vault/)`);
            console.log('');
        }
    }
} catch (e) {
    // vault dir read error — continue to qnotes
}

// --- Recent qnotes (last 7 days) ---
const qnotesDir = path.join(VAULT_DIR, 'qnotes');
try {
    if (fs.existsSync(qnotesDir)) {
        const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
        const recent = fs.readdirSync(qnotesDir)
            .filter(f => f.endsWith('.md'))
            .filter(f => {
                const stat = fs.statSync(path.join(qnotesDir, f));
                return stat.mtimeMs > cutoff;
            })
            .sort()
            .reverse();

        if (recent.length > 0) {
            console.log(`=== Recent Qnotes (${recent.length}) ===`);
            for (const file of recent) {
                const content = fs.readFileSync(path.join(qnotesDir, file), 'utf-8');
                // Extract first line of the blockquote (the thought text)
                const match = content.match(/^>\s*(.+)/m);
                const preview = match ? match[1].substring(0, 120) : '(empty)';
                console.log(`- ${file}: ${preview}...`);
            }
            console.log('=== END QNOTES ===');
            console.log('');
            console.log('Scan these qnotes for unresolved action items using search_thoughts(source_type="qnote").');
        }
    }
} catch (e) {
    // qnotes dir read error — skip
}

if (found) {
    console.log('Read the handoff and use rawthink MCP tools proactively throughout the session.');
} else {
    console.log('No handoff found — first session or /rtclose not yet run.');
}
