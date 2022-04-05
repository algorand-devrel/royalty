from pyteal import *


asset_key = Bytes("royalty_asset")
recv_key = Bytes("royalty_receiver")
share_key = Bytes("royalty_share")
allowed_key = Bytes("allowed_assets")

# A basis point is 1/100 of 1%
basis_point_multiplier = 100 * 100

return_prefix = Bytes("base16", "0x151f7c75")  # Literally hash('return')[:4]


create_selector = MethodSignature("create_nft()uint64")


@Subroutine(TealType.uint64)
def create_nft():
    return Seq(
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetConfig,
                TxnField.config_asset_name: Bytes("Royalty Asset"),
                TxnField.config_asset_unit_name: Bytes("ra"),
                TxnField.config_asset_total: Int(1),
                TxnField.config_asset_manager: Global.current_application_address(),
                TxnField.config_asset_reserve: Global.current_application_address(),
                TxnField.config_asset_freeze: Global.current_application_address(),
                TxnField.config_asset_clawback: Global.current_application_address(),
                TxnField.config_asset_default_frozen: Int(1),
            }
        ),
        InnerTxnBuilder.Submit(),
        Log(Concat(return_prefix, Itob(InnerTxn.created_asset_id()))),
        Int(1),
    )


set_policy_selector = MethodSignature(
    "set_policy(asset,address,uint64,asset,asset,asset,asset)void"
)


@Subroutine(TealType.uint64)
def set_policy():
    # TODO: this opts in any assets we need to but we arentopting out of assets from any previous policy

    # If the ID passed is 0, apply this policy as default for any transfers
    royalty_asset = Txn.assets[Btoi(Txn.application_args[1])]
    recv = Txn.application_args[2]
    share = Txn.application_args[3]

    buff = ScratchVar(TealType.bytes)
    curr_asset = ScratchVar(TealType.uint64)

    current_policy = App.globalGetEx(Int(0), Itob(royalty_asset))

    return Seq(
        current_policy,
        Assert(Not(current_policy.hasValue())),
        Assert(Btoi(share) < Int(basis_point_multiplier)),  # cant be > 10k or 100%
        buff.store(Bytes("")),
        If(royalty_asset != Int(0), ensure_opted_in(royalty_asset)),
        For(
            (i := ScratchVar()).store(Int(4)),
            i.load() < Txn.application_args.length(),
            i.store(i.load() + Int(1)),
        ).Do(
            Seq(
                curr_asset.store(Txn.assets[Btoi(Txn.application_args[i.load()])]),
                If(
                    curr_asset.load() != Int(0),
                    Seq(
                        ensure_opted_in(curr_asset.load()),
                        buff.store(Concat(buff.load(), Itob(curr_asset.load()))),
                    ),
                ),
            )
        ),
        App.globalPut(Itob(royalty_asset), Concat(recv, share, buff.load())),
        Int(1),
    )


transfer_selector = MethodSignature(
    "transfer(asset,account,account,account,uint64,txn,asset,uint64)void"
)


@Subroutine(TealType.uint64)
def transfer():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    owner_acct = Txn.accounts[Btoi(Txn.application_args[2])]
    buyer_acct = Txn.accounts[Btoi(Txn.application_args[3])]
    royalty_acct = Txn.accounts[Btoi(Txn.application_args[4])]
    asset_amt = Btoi(Txn.application_args[5])
    purchase_txn = Gtxn[Txn.group_index() - Int(1)]
    # dont need to use this, just rely on the asset id of the asset payment txn
    # asset_idx = Txn.application_args[6]
    curr_offered_amt = Btoi(Txn.application_args[7])

    policy = ScratchVar(TealType.bytes)

    # Get the auth_addr from local state of the owner
    # If its not present, a 0 is returned and the call fails when we try
    # to compare to the bytes of Txn.sender
    offer = App.localGet(owner_acct, Itob(asset_id))
    offer_auth_addr = Extract(offer, Int(0), Int(32))
    offer_amt = ExtractUint64(offer, Int(32))

    valid_transfer_group = And(
        Global.group_size() == Int(2),
        # App call sent by authorizing address
        Txn.sender() == offer_auth_addr,
        # No funny business
        purchase_txn.rekey_to() == Global.zero_address(),
        # payment txn should be from auth
        purchase_txn.sender() == offer_auth_addr,
        # transfer amount <= offered amount
        asset_amt <= offer_amt,
        # Passed the correct account according to the policy
        Or(
            And(
                purchase_txn.type_enum() == TxnType.AssetTransfer,
                # Just to be sure
                purchase_txn.asset_close_to() == Global.zero_address(),
                # Is this a valid asset id according to the spec?
                in_approved_list(purchase_txn.xfer_asset(), policy.load()),
                # Make sure payments go to the right participants
                purchase_txn.asset_receiver() == Global.current_application_address(),
            ),
            And(
                purchase_txn.type_enum() == TxnType.Payment,
                # Just to be sure
                purchase_txn.close_remainder_to() == Global.zero_address(),
                # Make sure payments are going to the right participants
                purchase_txn.receiver() == Global.current_application_address(),
            ),
        ),
        correct_royalty_receiver(royalty_acct, policy.load()),
    )

    return Seq(
        # initialize values
        (owner_auth := AccountParam.authAddr(owner_acct)),
        (buyer_auth := AccountParam.authAddr(buyer_acct)),
        policy.store(get_policy(asset_id)),
        #  Make sure we have address(32 bytes) and royalty amount(8 bytes)
        #  may have additional allowed assets
        Assert(Len(policy.load()) >= Int(40)),
        # Make sure transactions look right
        Assert(valid_transfer_group),
        # Make sure neither owner/buyer have been rekeyed
        Assert(owner_auth.value() == Global.zero_address()),
        Assert(buyer_auth.value() == Global.zero_address()),
        # Make royalty payment
        If(
            purchase_txn.type_enum() == TxnType.AssetTransfer,
            pay_assets(
                purchase_txn.xfer_asset(),
                purchase_txn.asset_amount(),
                owner_acct,
                royalty_acct,
                policy.load(),
            ),
            pay_algos(purchase_txn.amount(), owner_acct, royalty_acct, policy.load()),
        ),
        # Perform asset move
        move_asset(asset_id, owner_acct, buyer_acct, asset_amt),
        # Clear listing from local state of owner
        update_offered(
            owner_acct,
            Itob(asset_id),
            offer_auth_addr,
            offer_amt - asset_amt,
            Txn.sender(),
            curr_offered_amt,
        ),
        Int(1),
    )


offer_selector = MethodSignature("offer(asset,uint64,account,uint64,address)void")


@Subroutine(TealType.uint64)
def offer():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    asset_amt = Btoi(Txn.application_args[2])
    auth_acct = Txn.accounts[Btoi(Txn.application_args[3])]
    prev_amt = Btoi(Txn.application_args[4])
    prev_auth = Txn.application_args[5]

    return Seq(
        bal := AssetHolding.balance(Txn.sender(), asset_id),
        # Check that caller _has_ this asset
        Assert(bal.value() >= asset_amt),
        # Check that we have a policy for it
        Assert(Len(get_policy(asset_id)) > Int(0)),
        # Set the auth addr for this asset
        update_offered(
            Txn.sender(), Itob(asset_id), auth_acct, asset_amt, prev_auth, prev_amt
        ),
        Int(1),
    )


royalty_free_move_selector = MethodSignature(
    "royalty_free_move(asset,uint64,account,account,uint64,address)void"
)


@Subroutine(TealType.uint64)
def royalty_free_move():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    asset_amt = Btoi(Txn.application_args[2])
    from_acct = Txn.accounts[Btoi(Txn.application_args[3])]
    to_acct = Txn.accounts[Btoi(Txn.application_args[4])]

    prev_offered_amt = Btoi(Txn.application_args[5])
    prev_offered_auth = Txn.application_args[6]

    opted_in = App.optedIn(from_acct, Int(0))

    return Seq(
        # Delete any offer if it exists
        If(
            opted_in,
            update_offered(
                from_acct,
                Itob(asset_id),
                Bytes(""),
                Int(0),
                prev_offered_auth,
                prev_offered_amt,
            ),
        ),
        # Move it
        move_asset(asset_id, from_acct, to_acct, asset_amt),
        # Success!
        Int(1),
    )


@Subroutine(TealType.none)
def ensure_opted_in(asset_id):
    bal = AssetHolding.balance(Global.current_application_address(), asset_id)
    return Seq(
        bal,
        If(
            Not(bal.hasValue()),
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.AssetTransfer,
                        TxnField.xfer_asset: asset_id,
                        TxnField.asset_amount: Int(0),
                        TxnField.asset_receiver: Global.current_application_address(),
                    }
                ),
                InnerTxnBuilder.Submit(),
            ),
        ),
    )


@Subroutine(TealType.none)
def pay_assets(purchase_asset_id, purchase_amt, owner, royalty_receiver, policy):
    royalty_amt = ScratchVar()
    return Seq(
        royalty_amt.store(royalty_amount(purchase_amt, policy)),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: purchase_asset_id,
                TxnField.asset_amount: purchase_amt - royalty_amt.load(),
                TxnField.asset_receiver: owner,
            }
        ),
        InnerTxnBuilder.Next(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: purchase_asset_id,
                TxnField.asset_amount: royalty_amt.load(),
                TxnField.asset_receiver: royalty_receiver,
            }
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.none)
def pay_algos(purchase_amt, owner, royalty_receiver, policy):
    royalty_amt = ScratchVar()
    return Seq(
        royalty_amt.store(royalty_amount(purchase_amt, policy)),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.Payment,
                TxnField.amount: purchase_amt - royalty_amt.load(),
                TxnField.receiver: owner,
            }
        ),
        InnerTxnBuilder.Next(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.Payment,
                TxnField.amount: royalty_amt.load(),
                TxnField.receiver: royalty_receiver,
            }
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.uint64)
def royalty_amount(payment_amt, policy):
    return WideRatio(
        [payment_amt], [ExtractUint64(policy, Int(32)), Int(basis_point_multiplier)]
    )


@Subroutine(TealType.uint64)
def correct_royalty_receiver(addr, policy):
    # First 32 bytes are the royalty receiver
    return Extract(policy, Int(0), Int(32)) == addr


@Subroutine(TealType.uint64)
def in_approved_list(asset_id, policy):
    # Iterate over policy[40:] 8 bytes at a time to check
    # if the asset id is allowed
    i = ScratchVar()
    init = i.store(Int(0))
    cond = i.load() < (Len(policy) - Int(40)) / Int(8)
    iter = i.store(i.load() + Int(1))
    return Seq(
        For(init, cond, iter).Do(
            If(
                ExtractUint64(policy, Int(40) + i.load() * Int(8)) == asset_id,
                Return(Int(1)),
            )
        ),
        Int(0),
    )


@Subroutine(TealType.none)
def move_asset(asset_id, from_addr, to_addr, asset_amt):
    # TODO: should we check that this should be a close_to?
    return Seq(
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: asset_id,
                TxnField.asset_amount: asset_amt,
                TxnField.asset_sender: from_addr,
                TxnField.asset_receiver: to_addr,
            }
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.none)
def update_offered(acct, asset, auth, amt, prev_auth, prev_amt):
    return Seq(
        previous := App.localGetEx(acct, Int(0), asset),
        # If we had something before, make sure its the same as what was passed. Otherwise make sure that a 0 was passed
        If(
            previous.hasValue(),
            Assert(
                And(
                    offered_amount(previous.value()) == prev_amt,
                    offered_auth(previous.value()) == prev_auth,
                )
            ),
            Assert(And(prev_amt == Int(0), prev_auth == Global.zero_address())),
        ),
        # Now consider the new offer, if its 0 this is a delete, otherwise update
        If(
            amt > Int(0),
            App.localPut(acct, asset, Concat(auth, Itob(amt))),
            App.localDel(acct, asset),
        ),
    )


@Subroutine(TealType.bytes)
def get_policy(asset_id):
    # Returns a policy for the specific asset or the default policy if one is set
    default_policy = App.globalGetEx(Int(0), Itob(Int(0)))
    specific_policy = App.globalGetEx(Int(0), Itob(asset_id))
    return Seq(
        specific_policy,
        default_policy,
        Assert(Or(specific_policy.hasValue(), default_policy.hasValue())),
        If(specific_policy.hasValue(), specific_policy.value(), default_policy.value()),
    )


@Subroutine(TealType.uint64)
def offered_amount(offer):
    return ExtractUint64(offer, Int(32))


@Subroutine(TealType.bytes)
def offered_auth(offer):
    return Extract(offer, Int(0), Int(32))


def approval():
    from_creator = Txn.sender() == Global.creator_address()

    action_router = Cond(
        [And(Txn.application_args[0] == create_selector, from_creator), create_nft()],
        [
            And(Txn.application_args[0] == royalty_free_move_selector, from_creator),
            royalty_free_move(),
        ],
        [
            And(Txn.application_args[0] == set_policy_selector, from_creator),
            set_policy(),
        ],
        [Txn.application_args[0] == transfer_selector, transfer()],
        [Txn.application_args[0] == offer_selector, offer()],
    )

    return Cond(
        [Txn.application_id() == Int(0), Approve()],
        [Txn.on_completion() == OnComplete.DeleteApplication, Return(from_creator)],
        [Txn.on_completion() == OnComplete.UpdateApplication, Return(from_creator)],
        [Txn.on_completion() == OnComplete.OptIn, Approve()],
        [Txn.on_completion() == OnComplete.CloseOut, Approve()],
        [Txn.on_completion() == OnComplete.NoOp, Return(action_router)],
    )


def clear():
    return Approve()


def get_approval():
    return compileTeal(approval(), mode=Mode.Application, version=6)


def get_clear():
    return compileTeal(clear(), mode=Mode.Application, version=6)


if __name__ == "__main__":
    print(get_approval())
