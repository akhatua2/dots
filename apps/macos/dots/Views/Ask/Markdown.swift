import AppKit
import SwiftUI

/// An answer as it should read: headings, lists, code, quotes, tables and math, not the raw Markdown.
/// It is parsed again on every streamed update, so an unclosed code fence runs to the end until it closes.
struct MarkdownText: View {
    var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(Markdown.blocks(text).enumerated()), id: \.offset) { _, block in
                BlockView(block: block)
            }
        }
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct BlockView: View {
    var block: Markdown.Block

    var body: some View {
        switch block {
        case .heading(let level, let text):
            Text(Markdown.inline(text))
                .font(D.ui(level == 1 ? 20 : level == 2 ? 17.5 : 15.5, .semibold))
                .foregroundStyle(D.t1)
                .padding(.top, 4)
        case .paragraph(let text):
            Text(Markdown.inline(text)).font(D.ui(15)).foregroundStyle(D.t2).lineSpacing(3.5)
        case .list(let items):
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(item.marker).font(D.ui(15).monospacedDigit()).foregroundStyle(D.t4)
                        Text(Markdown.inline(item.text)).font(D.ui(15)).foregroundStyle(D.t2).lineSpacing(3.5)
                    }
                    .padding(.leading, CGFloat(item.depth) * 18)
                }
            }
        case .code(let language, let code):
            CodeBlock(language: language, code: code)
        case .math(let latex):
            Text(LaTeX.plain(latex))
                .font(.system(size: 17, design: .serif))
                .foregroundStyle(D.t1)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 2)
        case .quote(let text):
            HStack(spacing: 12) {
                Rectangle().fill(D.btn).frame(width: 2)
                Text(Markdown.inline(text)).font(D.ui(15)).foregroundStyle(D.t3).lineSpacing(3.5)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .table(let header, let rows):
            MarkdownTable(header: header, rows: rows)
        case .rule:
            Rectangle().fill(D.line).frame(height: 1).padding(.vertical, 4)
        }
    }
}

/// A code block with its language and a copy button, scrolling sideways rather than wrapping.
private struct CodeBlock: View {
    var language: String
    var code: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "code" : language).font(D.ui(11.5, .medium)).foregroundStyle(D.t4)
                Spacer()
                Button(action: copy) {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .font(D.ui(11.5))
                }
                .buttonStyle(.plain)
                .foregroundStyle(D.t4)
            }
            .padding(.horizontal, 14)
            .frame(height: 32)
            Rectangle().fill(D.line).frame(height: 1)
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(D.mono(13))
                    .foregroundStyle(D.t2)
                    .lineSpacing(3)
                    .fixedSize(horizontal: true, vertical: false)
                    .padding(14)
            }
        }
        .card(radius: 10, fill: D.deep)
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(code, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
    }
}

private struct MarkdownTable: View {
    var header: [String]
    var rows: [[String]]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 8) {
                GridRow {
                    ForEach(Array(header.enumerated()), id: \.offset) { _, cell in
                        Text(Markdown.inline(cell)).font(D.ui(13.5, .semibold)).foregroundStyle(D.t1)
                    }
                }
                Rectangle().fill(D.line).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Text(Markdown.inline(cell)).font(D.ui(13.5)).foregroundStyle(D.t2)
                        }
                    }
                }
            }
            .padding(.vertical, 2)
        }
    }
}

// MARK: - Parsing

enum Markdown {
    enum Block: Equatable {
        case heading(level: Int, text: String)
        case paragraph(String)
        case list([ListItem])
        case code(language: String, code: String)
        case math(String)
        case quote(String)
        case table(header: [String], rows: [[String]])
        case rule
    }

    struct ListItem: Equatable {
        var marker: String
        var text: String
        var depth: Int
    }

    /// Splits Markdown into blocks, line by line. Forgiving on purpose: small models write loose Markdown.
    static func blocks(_ source: String) -> [Block] {
        let lines = source.components(separatedBy: "\n")
        var blocks: [Block] = []
        var paragraph: [String] = []
        var i = 0

        func flush() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))) }
            paragraph = []
        }

        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.isEmpty {
                flush()
                i += 1
            } else if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flush()
                let fence = String(trimmed.prefix(3))
                let language = trimmed.dropFirst(3).trimmingCharacters(in: .whitespaces)
                var code: [String] = []
                i += 1
                while i < lines.count, !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(fence) {
                    code.append(lines[i])
                    i += 1
                }
                i += 1
                blocks.append(.code(language: language, code: dedent(code).joined(separator: "\n")))
            } else if let (open, close) = mathFence(trimmed) {
                flush()
                var body = String(trimmed.dropFirst(open.count))
                i += 1
                while !body.contains(close), i < lines.count {
                    body += "\n" + lines[i]
                    i += 1
                }
                if let end = body.range(of: close) { body = String(body[..<end.lowerBound]) }
                blocks.append(.math(body.trimmingCharacters(in: .whitespacesAndNewlines)))
            } else if let heading = heading(trimmed) {
                flush()
                blocks.append(heading)
                i += 1
            } else if isRule(trimmed) {
                flush()
                blocks.append(.rule)
                i += 1
            } else if trimmed.hasPrefix(">") {
                flush()
                var quote: [String] = []
                while i < lines.count, lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    quote.append(lines[i].trimmingCharacters(in: .whitespaces).dropFirst().trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                blocks.append(.quote(quote.joined(separator: "\n")))
            } else if trimmed.hasPrefix("|"), i + 1 < lines.count, isTableDivider(lines[i + 1]) {
                flush()
                let header = cells(trimmed)
                var rows: [[String]] = []
                i += 2
                while i < lines.count, lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("|") {
                    var row = cells(lines[i])
                    row += Array(repeating: "", count: max(0, header.count - row.count))
                    rows.append(Array(row.prefix(header.count)))
                    i += 1
                }
                blocks.append(.table(header: header, rows: rows))
            } else if listItem(line) != nil {
                flush()
                var items: [ListItem] = []
                while i < lines.count {
                    if let item = listItem(lines[i]) {
                        items.append(item)
                        i += 1
                    } else if let last = items.indices.last, isContinuation(lines[i]) {
                        items[last].text += "\n" + lines[i].trimmingCharacters(in: .whitespaces)
                        i += 1
                    } else if lines[i].trimmingCharacters(in: .whitespaces).isEmpty,
                              i + 1 < lines.count, listItem(lines[i + 1]) != nil {
                        i += 1 // a blank line between items keeps the list going
                    } else {
                        break
                    }
                }
                blocks.append(.list(items))
            } else {
                paragraph.append(trimmed)
                i += 1
            }
        }
        flush()
        return blocks
    }

    /// Bold, italics, inline code, links and inline math, styled for the answer.
    static func inline(_ text: String) -> AttributedString {
        let source = inlineMath(text)
        var out = (try? AttributedString(markdown: source, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(source)
        for run in out.runs {
            if run.inlinePresentationIntent?.contains(.code) == true {
                out[run.range].font = D.mono(13.5)
                out[run.range].foregroundColor = D.t1
                out[run.range].backgroundColor = Color.white.opacity(0.07)
            }
            if run.link != nil {
                out[run.range].foregroundColor = D.accentText
            }
        }
        return out
    }

    // MARK: Pieces

    private static func mathFence(_ line: String) -> (String, String)? {
        if line.hasPrefix("$$") { return ("$$", "$$") }
        if line.hasPrefix("\\[") { return ("\\[", "\\]") }
        return nil
    }

    private static func heading(_ line: String) -> Block? {
        let hashes = line.prefix { $0 == "#" }.count
        guard (1...6).contains(hashes), line.dropFirst(hashes).first == " " else { return nil }
        let text = line.dropFirst(hashes).trimmingCharacters(in: CharacterSet(charactersIn: "# "))
        return .heading(level: hashes, text: text)
    }

    private static func isRule(_ line: String) -> Bool {
        let chars = line.filter { $0 != " " }
        guard chars.count >= 3, let first = chars.first, "-*_".contains(first) else { return false }
        return chars.allSatisfy { $0 == first }
    }

    private static func isTableDivider(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix("|") && t.contains("-") && t.allSatisfy { "|-: ".contains($0) }
    }

    private static func cells(_ line: String) -> [String] {
        var t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    }

    /// "- item", "* item", "1. item" or "1) item", with its nesting from the indent.
    private static func listItem(_ line: String) -> ListItem? {
        let indent = line.prefix { $0 == " " || $0 == "\t" }.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
        let rest = line.drop { $0 == " " || $0 == "\t" }
        guard let space = rest.firstIndex(of: " ") else { return nil }
        let marker = rest[..<space]
        let text = rest[space...].trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return nil }
        if marker == "-" || marker == "*" || marker == "+" {
            return ListItem(marker: "•", text: text, depth: indent / 2)
        }
        if let last = marker.last, last == "." || last == ")", marker.count <= 4, marker.dropLast().allSatisfy(\.isNumber), marker.count > 1 {
            return ListItem(marker: marker.dropLast() + ".", text: text, depth: indent / 2)
        }
        return nil
    }

    /// An indented line under a list item that belongs to it, not a block of its own.
    private static func isContinuation(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return line.hasPrefix("  ") && !t.isEmpty && !t.hasPrefix("```") && mathFence(t) == nil
    }

    /// Code without its common indent or the blank lines around it.
    private static func dedent(_ lines: [String]) -> [String] {
        let isBlank = { (line: String) in line.trimmingCharacters(in: .whitespaces).isEmpty }
        let lines = Array(lines.drop(while: isBlank).reversed().drop(while: isBlank).reversed())
        let indent = lines.filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            .map { $0.prefix { $0 == " " }.count }.min() ?? 0
        return lines.map { String($0.dropFirst(min(indent, $0.prefix { $0 == " " }.count))) }
    }

    /// `\( x^2 \)` and `$x^2$` become readable text in place. A lone "$5" is left alone.
    private static func inlineMath(_ text: String) -> String {
        var out = text
        for (open, close) in [("\\(", "\\)"), ("$", "$")] {
            var searchFrom = out.startIndex
            while let start = out.range(of: open, range: searchFrom..<out.endIndex),
                  let end = out.range(of: close, range: start.upperBound..<out.endIndex) {
                let inner = String(out[start.upperBound..<end.lowerBound])
                let looksLikeMath = !inner.isEmpty && inner.first != " " && inner.last != " " && !inner.contains("\n")
                if open == "$", !looksLikeMath {
                    searchFrom = start.upperBound
                    continue
                }
                let plain = LaTeX.plain(inner)
                out.replaceSubrange(start.lowerBound..<end.upperBound, with: plain)
                searchFrom = out.index(start.lowerBound, offsetBy: plain.count, limitedBy: out.endIndex) ?? out.endIndex
            }
        }
        return out
    }
}

// MARK: - Math

/// LaTeX as plain text that reads well: `\frac{a}{b}` is a/b, `x^2` is x², `\alpha` is α.
/// Not a typesetter, but enough for the math a chat answer holds.
enum LaTeX {
    static func plain(_ latex: String) -> String {
        var reader = Reader(chars: Array(latex))
        return reader.run(until: nil)
            .replacingOccurrences(of: "  ", with: " ")
            .trimmingCharacters(in: .whitespaces)
    }

    private struct Reader {
        var chars: [Character]
        var i = 0

        mutating func run(until close: Character?) -> String {
            var out = ""
            while i < chars.count {
                if let close, chars[i] == close {
                    i += 1
                    return out
                }
                out += atom()
            }
            return out
        }

        /// One token: a group, a command with its arguments, a script, or a character.
        mutating func atom() -> String {
            let c = chars[i]
            i += 1
            switch c {
            case "{": return run(until: "}")
            case "}": return ""
            case "^": return script(group(), table: LaTeX.superscripts, mark: "^")
            case "_": return script(group(), table: LaTeX.subscripts, mark: "_")
            case "&", "~": return " "
            case "\\": return command()
            default: return String(c)
            }
        }

        /// A command's argument: a braced group or the next single token.
        mutating func group() -> String {
            while i < chars.count, chars[i] == " " { i += 1 }
            return i < chars.count ? atom() : ""
        }

        mutating func command() -> String {
            guard i < chars.count else { return "" }
            if !chars[i].isLetter {
                let c = chars[i]
                i += 1
                switch c {
                case ",", ";", ":", " ": return " "
                case "!": return ""
                case "\\": return "  "
                default: return String(c)
                }
            }
            var name = ""
            while i < chars.count, chars[i].isLetter {
                name.append(chars[i])
                i += 1
            }
            switch name {
            case "frac", "dfrac", "tfrac":
                let top = group(), bottom = group()
                return "\(wrap(top))/\(wrap(bottom))"
            case "sqrt":
                var root = ""
                if i < chars.count, chars[i] == "[" {
                    i += 1
                    root = run(until: "]")
                }
                let index = root.isEmpty ? "" : script(root, table: LaTeX.superscripts, mark: "^")
                return index + "√" + wrap(group())
            case "text", "mathrm", "mathbf", "mathit", "textbf", "textit", "operatorname", "mathsf", "boldsymbol":
                return group()
            case "mathbb":
                let letter = group()
                return LaTeX.blackboard[letter] ?? letter
            case "left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "limits":
                return ""
            case "quad", "qquad":
                return "  "
            default:
                return LaTeX.symbols[name] ?? name
            }
        }

        private func wrap(_ s: String) -> String {
            s.count <= 1 || s.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "." }) ? s : "(\(s))"
        }

        private func script(_ s: String, table: [Character: Character], mark: String) -> String {
            let mapped = s.compactMap { table[$0] }
            if !s.isEmpty, mapped.count == s.count { return String(mapped) }
            return s.count == 1 ? mark + s : "\(mark)(\(s))"
        }
    }

    static let superscripts: [Character: Character] = [
        "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
        "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ", "x": "ˣ", "T": "ᵀ",
        "k": "ᵏ", "m": "ᵐ", "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "t": "ᵗ", "∘": "°", "′": "′",
    ]

    static let subscripts: [Character: Character] = [
        "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
        "+": "₊", "-": "₋", "−": "₋", "=": "₌", "(": "₍", ")": "₎", "a": "ₐ", "e": "ₑ", "i": "ᵢ", "j": "ⱼ",
        "k": "ₖ", "n": "ₙ", "o": "ₒ", "x": "ₓ", "m": "ₘ", "t": "ₜ", "r": "ᵣ", "s": "ₛ", "p": "ₚ",
    ]

    static let blackboard: [String: String] = ["R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ"]

    static let symbols: [String: String] = [
        // Greek
        "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
        "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
        "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
        "chi": "χ", "psi": "ψ", "omega": "ω",
        "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
        "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
        // Operators and relations
        "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗", "star": "⋆", "circ": "∘",
        "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡",
        "sim": "∼", "propto": "∝", "ll": "≪", "gg": "≫",
        "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮", "partial": "∂", "nabla": "∇", "infty": "∞",
        "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "cup": "∪", "cap": "∩",
        "emptyset": "∅", "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨", "wedge": "∧", "vee": "∨",
        "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐",
        "leftrightarrow": "↔", "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔", "mapsto": "↦",
        "ldots": "…", "cdots": "⋯", "dots": "…", "vdots": "⋮", "prime": "′", "degree": "°",
        "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
        "sin": "sin", "cos": "cos", "tan": "tan", "log": "log", "ln": "ln", "exp": "exp", "lim": "lim",
        "max": "max", "min": "min",
    ]
}
