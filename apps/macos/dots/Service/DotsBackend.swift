import Foundation

/// Everything the app asks of the dots network, in one place.
///
/// Today `Simulation` answers all of it on this Mac. The real hub can take its place by
/// conforming to this protocol: the app's screens only ever talk to a `DotsBackend`.
@MainActor
protocol DotsBackend: AnyObject {
    var state: NetworkState { get }

    // Pools
    /// Joins a pool to ask it, lending it `gb` when that is more than 0
    func join(poolID: String, gb: Int) -> JoinOutcome
    /// How much this Mac lends a pool it is in; 0 stops lending there
    func setShare(poolID: String, gb: Int)
    func leave(poolID: String)
    func endorse(poolID: String) -> Badge?
    func createPool(name: String, isPublic: Bool) -> Pool
    func createInvite(poolID: String) -> Invite
    func revokeInvite(poolID: String)
    func setOffersIdle(poolID: String, _ on: Bool)
    func pledgeBoost(poolID: String, gb: Int?)

    // Lending
    func updateLending(_ change: (inout LendingSettings) -> Void)
    /// Moves this Mac in or out of its pools' lending sets, and reports an unlock it caused.
    func applyLendingStatus(active: Bool) -> UnlockEvent?

    // Credits and usage
    func spend(_ credits: Int, note: String)
    func record(_ request: RequestRecord)
    func setMemberAsleep(macID: String, poolID: String, asleep: Bool)

    // Keys for your own apps
    func createKey(name: String, reach: Reach, monthlyLimit: Int?) -> (key: APIKey, secret: String)
    func revokeKey(id: String)
    func key(forSecret secret: String) -> APIKey?
    func noteKeyUse(id: String, credits: Int)

    func rename(_ name: String)
    func markBadgesSeen()
    func tick(seconds: Double, lendingActive: Bool, working: Bool)
    func reset()
    func saveNow()
}

struct JoinOutcome {
    var macNumber: Int
    var pool: Pool
}
