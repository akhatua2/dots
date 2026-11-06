import Foundation

/// A model's list price, in dollars per million tokens.
struct ModelPrice: Codable, Hashable {
    var input: Double
    var output: Double
}

/// The gateway's terms: what a credit is worth, the discount on frontier models, and their list prices.
/// The cloud sends them with every sync; these defaults are the mock's.
struct Terms: Codable, Hashable {
    var creditUSD = 0.001
    var discount = 0.30
    /// Anthropic's list prices, checked 2026-10-18
    var prices: [String: ModelPrice] = [
        "claude-opus-5-5": ModelPrice(input: 4, output: 20),
        "claude-sonnet-5-5": ModelPrice(input: 2, output: 10),
        "claude-haiku-5-5": ModelPrice(input: 0.10, output: 0.50),
        "claude-fable-5-1": ModelPrice(input: 10, output: 50),
    ]
    /// An everyday answer, for "about N answers" and for estimates before an answer runs
    var typicalInput = 1_500
    var typicalOutput = 600
}

/// What answers cost, in dollars at list price and in credits through the gateway. One place for the
/// router, receipts, the savings on the API page and the mock gateway.
enum Pricing {
    static var terms = Terms()

    static func usd(_ modelID: String, input: Int, output: Int) -> Double {
        guard let p = terms.prices[modelID] else { return 0 }
        return (Double(input) * p.input + Double(output) * p.output) / 1_000_000
    }

    /// Credits the gateway charges: list price less the discount, at what a credit is worth. At least one.
    static func credits(_ modelID: String, input: Int, output: Int) -> Int {
        let usd = usd(modelID, input: input, output: output) * (1 - terms.discount)
        return max(1, Int((usd / terms.creditUSD).rounded(.up)))
    }

    static func listCredits(_ modelID: String, input: Int, output: Int) -> Int {
        max(1, Int((usd(modelID, input: input, output: output) / terms.creditUSD).rounded(.up)))
    }

    static func typicalCredits(_ modelID: String) -> Int { credits(modelID, input: terms.typicalInput, output: terms.typicalOutput) }
    static func typicalListCredits(_ modelID: String) -> Int { listCredits(modelID, input: terms.typicalInput, output: terms.typicalOutput) }

    /// How many everyday answers from a frontier model a balance buys.
    static func answers(_ credits: Double, modelID: String = FrontierModel.opus.id) -> Int {
        max(0, Int(credits / Double(typicalCredits(modelID))))
    }

    static func dollars(credits: Double) -> Double { credits * terms.creditUSD }

    /// A rough token count for text, when a backend doesn't report one: about four characters a token.
    static func tokens(_ text: String) -> Int { max(1, Int((Double(text.count) / 4).rounded(.up))) }
}

extension Double {
    /// "$0.0126", "$1.24", "$20", "$1,240"
    var dollars: String {
        if self == 0 { return "$0" }
        if self >= 1, self == self.rounded() { return "$" + Int(self).grouped }
        if self < 0.01 { return String(format: "$%.4f", self) }
        if self < 100 { return String(format: "$%.2f", self) }
        return "$" + Int(self.rounded()).grouped
    }
}
