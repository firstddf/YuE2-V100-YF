<#
.SYNOPSIS
    Apply the local YuE2 ABC-export patch to the vendored audio.cpp tree.

.DESCRIPTION
    Adds BPE detokenization and surfaces the generated ABC score as the task's
    text output, so `--text-out score.abc` (and the HTTP API) can return editable
    notation. Without this the score is generated and then discarded: only its
    token ids survive, and Yue2TextTokenizer has no decode().

    Every edit is a literal, occurrence-checked replacement. The inserted text is
    rewritten with the line ending each target file already uses, because these
    files are CRLF (some are mixed) and normalizing them would bury the real diff.

    Idempotent: re-running reports "already applied" instead of duplicating.

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\apply-abc-patch.ps1
.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\apply-abc-patch.ps1 -Revert
#>
[CmdletBinding()]
param(
    [string] $Repo = (Join-Path (Split-Path $PSScriptRoot -Parent) 'audio.cpp'),
    [switch] $Revert
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path (Join-Path $Repo 'CMakeLists.txt'))) { throw "Not an audio.cpp tree: $Repo" }

$script:Applied = 0
$script:Skipped = 0
$script:Failed  = 0

function Get-DominantEol([string] $text) {
    $crlf = ([regex]::Matches($text, "`r`n")).Count
    $lf   = ([regex]::Matches($text, "(?<!`r)`n")).Count
    if ($crlf -ge $lf) { return "`r`n" } else { return "`n" }
}

function Apply-Edit {
    param([string] $Path, [string] $Old, [string] $New, [string] $Label)

    $full = Join-Path $Repo $Path
    if (-not (Test-Path $full)) { Write-Host "  FAIL  $Label : missing $Path" -ForegroundColor Red; $script:Failed++; return }

    $text = [System.IO.File]::ReadAllText($full)
    $eol  = Get-DominantEol $text

    # Rewrite both sides with the file's own line ending, then match verbatim.
    $oldText = ($Old -replace "`r`n", "`n") -replace "`n", $eol
    $newText = ($New -replace "`r`n", "`n") -replace "`n", $eol

    if ($Revert) {
        $tmp = $oldText; $oldText = $newText; $newText = $tmp
    }

    $hits = ([regex]::Matches($text, [regex]::Escape($oldText))).Count
    if ($hits -eq 0) {
        if (([regex]::Matches($text, [regex]::Escape($newText))).Count -ge 1) {
            Write-Host "  SKIP  $Label : already applied" -ForegroundColor Yellow
            $script:Skipped++
        } else {
            Write-Host "  FAIL  $Label : anchor not found in $Path" -ForegroundColor Red
            $script:Failed++
        }
        return
    }
    if ($hits -ne 1) { Write-Host "  FAIL  $Label : anchor matched $hits times" -ForegroundColor Red; $script:Failed++; return }

    $text = $text.Replace($oldText, $newText)
    [System.IO.File]::WriteAllText($full, $text, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "  OK    $Label" -ForegroundColor Green
    $script:Applied++
}

Write-Host ("{0} ABC-export patch in {1}" -f $(if ($Revert) { 'Reverting' } else { 'Applying' }), $Repo) -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. tokenizer header
Apply-Edit -Label 'tokenizer_text.h: declare decode()' `
  -Path 'include/engine/models/yue2/tokenizer_text.h' `
  -Old @'
    std::vector<int32_t> encode(const std::string & text) const;
'@ `
  -New @'
    std::vector<int32_t> encode(const std::string & text) const;

    // [yue2-V100 local patch] Inverse of encode(). Structural tokens (<abc>,
    // </abc>, <music>, </music> and the other control tokens) carry no notation
    // text and are skipped, which is what makes the generated ABC score
    // recoverable as plain notation.
    std::string decode(const std::vector<int32_t> & ids) const;
'@

# ---------------------------------------------------------------- 2. tokenizer includes
Apply-Edit -Label 'tokenizer_text.cpp: include <unordered_map>' `
  -Path 'src/models/yue2/tokenizer_text.cpp' `
  -Old @'
#include <stdexcept>
#include <string>
'@ `
  -New @'
#include <stdexcept>
#include <string>
#include <unordered_map>
'@

# ---------------------------------------------------------------- 3. byte unmapping helper
Apply-Edit -Label 'tokenizer_text.cpp: add unmap_token_bytes()' `
  -Path 'src/models/yue2/tokenizer_text.cpp' `
  -Old @'
std::string pair_key(const std::string & left, const std::string & right) {
'@ `
  -New @'
// [yue2-V100 local patch] Inverse of map_token_bytes(): the tiktoken vocabulary
// stores each byte as a UTF-8-encoded code point, so decoding has to fold those
// code points back into their original bytes. The reverse table is built from
// the very mapping encode() uses, so the two cannot drift apart.
std::string unmap_token_bytes(const std::string & mapped) {
    static const std::unordered_map<std::string, char> reverse = [] {
        std::unordered_map<std::string, char> table;
        for (int value = 0; value < 256; ++value) {
            table.emplace(unicode_byte_to_utf8(static_cast<uint8_t>(value)), static_cast<char>(value));
        }
        return table;
    }();

    std::string bytes;
    size_t offset = 0;
    while (offset < mapped.size()) {
        const auto lead = static_cast<unsigned char>(mapped[offset]);
        size_t length = lead >= 0xF0 ? 4 : lead >= 0xE0 ? 3 : lead >= 0xC0 ? 2 : 1;
        if (offset + length > mapped.size()) {
            length = 1;
        }
        const auto found = reverse.find(mapped.substr(offset, length));
        if (found == reverse.end()) {
            bytes.append(mapped, offset, length);
        } else {
            bytes.push_back(found->second);
        }
        offset += length;
    }
    return bytes;
}

std::string pair_key(const std::string & left, const std::string & right) {
'@

# ---------------------------------------------------------------- 4. decode() implementation
Apply-Edit -Label 'tokenizer_text.cpp: implement decode()' `
  -Path 'src/models/yue2/tokenizer_text.cpp' `
  -Old @'
std::vector<int32_t> Yue2TextTokenizer::encode(const std::string & text) const {
    return vendor::tokenize_bpe(*vocab_, text, true);
}
'@ `
  -New @'
std::vector<int32_t> Yue2TextTokenizer::encode(const std::string & text) const {
    return vendor::tokenize_bpe(*vocab_, text, true);
}

std::string Yue2TextTokenizer::decode(const std::vector<int32_t> & ids) const {
    std::string bytes;
    bytes.reserve(ids.size() * 2);
    for (const int32_t id : ids) {
        const auto found = vocab_->id_to_token.find(id);
        if (found == vocab_->id_to_token.end()) {
            continue;
        }
        if ((found->second.attr & vendor::TOKEN_ATTR_CONTROL) != 0) {
            continue;
        }
        bytes += unmap_token_bytes(found->second.text);
    }
    return bytes;
}
'@

# ---------------------------------------------------------------- 5. pipeline header
Apply-Edit -Label 'pipeline.h: declare last_abc()' `
  -Path 'include/engine/models/yue2/pipeline.h' `
  -Old @'
    runtime::AudioBuffer run(const Yue2Request & request);
'@ `
  -New @'
    runtime::AudioBuffer run(const Yue2Request & request);
    // [yue2-V100 local patch] ABC score produced by the most recent run(); empty
    // when the request used cot=off or supplied its own score.
    std::string last_abc() const;
'@

# ---------------------------------------------------------------- 6. pipeline Impl accessor
Apply-Edit -Label 'pipeline.cpp: Impl keeps last_abc_' `
  -Path 'src/models/yue2/pipeline.cpp' `
  -Old @'
        (void) this->nar_graph_arena_bytes;
    }
'@ `
  -New @'
        (void) this->nar_graph_arena_bytes;
    }

    // [yue2-V100 local patch] Score from the most recent generate_semantic();
    // run() copies it here so the session can expose it as the text output.
    const std::string & last_abc() const { return last_abc_; }

    std::string last_abc_;
'@

# ---------------------------------------------------------------- 7. decode the score
Apply-Edit -Label 'pipeline.cpp: decode generated ABC' `
  -Path 'src/models/yue2/pipeline.cpp' `
  -Old @'
            out.plan.truncated = static_cast<int64_t>(out.plan.abc_ids.size()) >= request.generation.abc.max_tokens;
            out.plan.prefix.insert(out.plan.prefix.end(), out.plan.abc_ids.begin(), out.plan.abc_ids.end());
'@ `
  -New @'
            out.plan.truncated = static_cast<int64_t>(out.plan.abc_ids.size()) >= request.generation.abc.max_tokens;
            // [yue2-V100 local patch] Recover the notation; otherwise the score is
            // dropped here and only its token ids survive.
            out.plan.abc = tokenizer.decode(out.plan.abc_ids);
            engine::debug::timing_log_scalar("yue2.semantic.abc_text_bytes", out.plan.abc.size());
            out.plan.prefix.insert(out.plan.prefix.end(), out.plan.abc_ids.begin(), out.plan.abc_ids.end());
'@

# ---------------------------------------------------------------- 8. run() captures it
Apply-Edit -Label 'pipeline.cpp: run() stores the score' `
  -Path 'src/models/yue2/pipeline.cpp' `
  -Old @'
        auto semantic = generate_semantic(request, std::move(planned));
'@ `
  -New @'
        auto semantic = generate_semantic(request, std::move(planned));
        last_abc_ = semantic.plan.abc;  // [yue2-V100 local patch]
'@

# ---------------------------------------------------------------- 9. public wrapper
Apply-Edit -Label 'pipeline.cpp: expose last_abc()' `
  -Path 'src/models/yue2/pipeline.cpp' `
  -Old @'
runtime::AudioBuffer Yue2PipelineRuntime::run(const Yue2Request & request) {
    return impl_->run(request);
}
'@ `
  -New @'
runtime::AudioBuffer Yue2PipelineRuntime::run(const Yue2Request & request) {
    return impl_->run(request);
}

std::string Yue2PipelineRuntime::last_abc() const {
    return impl_->last_abc();
}
'@

# ---------------------------------------------------------------- 10. session surfaces it
Apply-Edit -Label 'session.cpp: set result.text_output' `
  -Path 'src/models/yue2/session.cpp' `
  -Old @'
    runtime::TaskResult result;
    result.audio_output = pipeline_->run(parsed);
'@ `
  -New @'
    runtime::TaskResult result;
    result.audio_output = pipeline_->run(parsed);
    // [yue2-V100 local patch] Expose the generated ABC score as the task text
    // output so the CLI --text-out and the HTTP API can return editable
    // notation. Empty for cot=off and for externally supplied scores.
    if (const std::string abc = pipeline_->last_abc(); !abc.empty()) {
        result.text_output = runtime::Transcript{abc, {}};
    }
'@

Write-Host ''
Write-Host ("applied={0} skipped={1} failed={2}" -f $script:Applied, $script:Skipped, $script:Failed)
if ($script:Failed -gt 0) { exit 1 }
if (-not $Revert -and $script:Applied -gt 0) {
    Write-Host ''
    Write-Host 'diff stat:' -ForegroundColor Cyan
    & git -C $Repo diff --stat
}
