from pyteal import *


r_basis_key = Bytes("royalty_basis")
r_recv_key = Bytes("royalty_receiver")

# A basis point is 1/100 of 1%
basis_point_multiplier = 100 * 100

return_prefix = Bytes("base16", "0x151f7c75")  # Literally hash('return')[:4]


@Subroutine(TealType.bytes)
def royalty_receiver():
    return App.globalGet(r_recv_key)


@Subroutine(TealType.uint64)
def royalty_basis():
    return App.globalGet(r_basis_key)


@Subroutine(TealType.uint64)
def offered_amount(offer):
    return ExtractUint64(offer, Int(32))


@Subroutine(TealType.bytes)
def offered_auth(offer):
    return Extract(offer, Int(0), Int(32))


set_policy_selector = MethodSignature("set_royalty_policy(uint64,account)void")


@Subroutine(TealType.uint64)
def set_policy():
    r_basis = Btoi(Txn.application_args[1])
    r_recv = Txn.accounts[Btoi(Txn.application_args[2])]
    return Seq(
        (r_basis_stored := App.globalGetEx(Int(0), r_basis_key)),
        (r_recv_stored := App.globalGetEx(Int(0), r_recv_key)),
        Assert(Not(r_basis_stored.hasValue())),
        Assert(Not(r_recv_stored.hasValue())),
        Assert(r_basis <= Int(basis_point_multiplier)),
        App.globalPut(r_basis_key, r_basis),
        App.globalPut(r_recv_key, r_recv),
        Int(1),
    )


set_asset_selector = MethodSignature("set_payment_asset(asset,bool)void")


@Subroutine(TealType.uint64)
def set_asset():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    is_allowed = Btoi(Txn.application_args[2])

    return Seq(
        bal := AssetHolding.balance(Global.current_application_address(), asset_id),
        creator := AssetParam.creator(asset_id),
        If(And(is_allowed, Not(bal.hasValue())))
        .Then(
            # Opt in to asset
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
        )
        .ElseIf(And(Not(is_allowed), bal.hasValue()))
        .Then(
            # Opt out, close asset to asset creator
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.AssetTransfer,
                        TxnField.xfer_asset: asset_id,
                        TxnField.asset_amount: Int(0),
                        TxnField.asset_close_to: creator.value(),
                        TxnField.asset_receiver: creator.value(),
                    }
                ),
                InnerTxnBuilder.Submit(),
            ),
        ),
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
    # Unusued, just passed in args to let the app have access in foreign assets
    # asset_idx  = Txn.application_args[6]
    curr_offered_amt = Btoi(Txn.application_args[7])

    # Get the auth_addr from local state of the owner
    # If its not present, a 0 is returned and the call fails when we try
    # to compare to the bytes of Txn.sender
    offer = App.localGet(owner_acct, Itob(asset_id))
    offer_auth_addr = offered_auth(offer)
    offer_amt = offered_amount(offer)

    stored_royalty_recv = ScratchVar(TealType.bytes)
    stored_royalty_basis = ScratchVar(TealType.uint64)

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
        royalty_acct == stored_royalty_recv.load(),
    )

    return Seq(
        # initialize values to check rekey
        (owner_auth := AccountParam.authAddr(owner_acct)),
        (buyer_auth := AccountParam.authAddr(buyer_acct)),
        # Make sure neither owner/buyer have been rekeyed (OPTIONAL)
        Assert(owner_auth.value() == Global.zero_address()),
        Assert(buyer_auth.value() == Global.zero_address()),
        # Grab the royalty policy settings
        stored_royalty_recv.store(royalty_receiver()),
        stored_royalty_basis.store(royalty_basis()),
        # Make sure transactions look right
        Assert(valid_transfer_group),
        # Make royalty payment
        If(
            purchase_txn.type_enum() == TxnType.AssetTransfer,
            pay_assets(
                purchase_txn.xfer_asset(),
                purchase_txn.asset_amount(),
                owner_acct,
                royalty_acct,
                stored_royalty_basis.load(),
            ),
            pay_algos(
                purchase_txn.amount(),
                owner_acct,
                royalty_acct,
                stored_royalty_basis.load(),
            ),
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
        cb := AssetParam.clawback(asset_id),
        bal := AssetHolding.balance(Txn.sender(), asset_id),
        # Check that caller _has_ this asset
        Assert(bal.value() >= asset_amt),
        # Check that this app is the clawback for it
        Assert(And(cb.hasValue(), cb.value() == Global.current_application_address())),
        # Set the auth addr for this asset
        update_offered(
            Txn.sender(), Itob(asset_id), auth_acct, asset_amt, prev_auth, prev_amt
        ),
        Int(1),
    )


royalty_free_move_selector = MethodSignature(
    "royalty_free_move(asset,uint64,account,account,uint64,account)void"
)


@Subroutine(TealType.uint64)
def royalty_free_move():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    asset_amt = Btoi(Txn.application_args[2])

    from_acct = Txn.accounts[Btoi(Txn.application_args[3])]
    to_acct = Txn.accounts[Btoi(Txn.application_args[4])]

    prev_offered_amt = Btoi(Txn.application_args[5])
    prev_offered_auth = Txn.accounts[Btoi(Txn.application_args[6])]

    offer = App.localGet(from_acct, Itob(asset_id))

    curr_offer_amt = ScratchVar()
    curr_offer_auth = ScratchVar()
    return Seq(
        curr_offer_amt.store(offered_amount(offer)),
        curr_offer_auth.store(offered_auth(offer)),
        # Must match what is currently offered
        Assert(curr_offer_amt.load() == prev_offered_amt),
        Assert(curr_offer_auth.load() == prev_offered_auth),
        # Must be set to app creator and less than the amount to move
        Assert(curr_offer_auth.load() == Global.creator_address()),
        Assert(curr_offer_amt.load() <= asset_amt),
        # Delete the offer
        update_offered(
            from_acct,
            Itob(asset_id),
            Bytes(""),
            Int(0),
            prev_offered_auth,
            prev_offered_amt,
        ),
        # Move it
        move_asset(asset_id, from_acct, to_acct, asset_amt),
        Int(1),
    )


@Subroutine(TealType.none)
def pay_assets(purchase_asset_id, purchase_amt, owner, royalty_receiver, royalty_basis):
    royalty_amt = ScratchVar()
    return Seq(
        royalty_amt.store(royalty_amount(purchase_amt, royalty_basis)),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: purchase_asset_id,
                TxnField.asset_amount: purchase_amt - royalty_amt.load(),
                TxnField.asset_receiver: owner,
            }
        ),
        If(
            royalty_amt.load() > Int(0),
            Seq(
                InnerTxnBuilder.Next(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.AssetTransfer,
                        TxnField.xfer_asset: purchase_asset_id,
                        TxnField.asset_amount: royalty_amt.load(),
                        TxnField.asset_receiver: royalty_receiver,
                    }
                ),
            ),
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.none)
def pay_algos(purchase_amt, owner, royalty_receiver, royalty_basis):
    royalty_amt = ScratchVar()
    return Seq(
        royalty_amt.store(royalty_amount(purchase_amt, royalty_basis)),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.Payment,
                TxnField.amount: purchase_amt - royalty_amt.load(),
                TxnField.receiver: owner,
            }
        ),
        If(
            royalty_amt.load() > Int(0),
            Seq(
                InnerTxnBuilder.Next(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.Payment,
                        TxnField.amount: royalty_amt.load(),
                        TxnField.receiver: royalty_receiver,
                    }
                ),
            ),
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.uint64)
def royalty_amount(payment_amt, royalty_basis):
    return WideRatio([payment_amt, royalty_basis], [Int(basis_point_multiplier)])


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


def approval():
    from_creator = Txn.sender() == Global.creator_address()

    action_router = Cond(
        [
            And(Txn.application_args[0] == royalty_free_move_selector, from_creator),
            royalty_free_move(),
        ],
        [
            And(Txn.application_args[0] == set_policy_selector, from_creator),
            set_policy(),
        ],
        [
            And(Txn.application_args[0] == set_asset_selector, from_creator),
            set_asset(),
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
    return compileTeal(
        approval(),
        mode=Mode.Application,
        version=6,
        optimize=OptimizeOptions(scratch_slots=True),
    )


def get_clear():
    return compileTeal(clear(), mode=Mode.Application, version=6)


if __name__ == "__main__":
    print(get_approval())
