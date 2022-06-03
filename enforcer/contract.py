from pyteal import *
from typing import Literal


administrator_key = Bytes("administrator")
r_basis_key = Bytes("royalty_basis")
r_recv_key = Bytes("royalty_receiver")

# A basis point is 1/100 of 1%
basis_point_multiplier = 100 * 100


Offer = abi.Tuple2[abi.Address, abi.Uint64]
Policy = abi.Tuple2[abi.Address, abi.Uint64]


@Subroutine(TealType.bytes)
def get_admin():
    return Seq(
        (admin := App.globalGetEx(Int(0), administrator_key)),
        If(admin.hasValue(), admin.value(), Global.creator_address()),
    )


@Subroutine(TealType.none)
def put_admin(admin: Expr):
    return App.globalPut(administrator_key, admin)


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


from_administrator = Txn.sender() == get_admin()

router = Router(
    "demo-arc-18",
    BareCallActions(
        no_op=OnCompleteAction.create_only(Seq(put_admin(Txn.sender()), Approve())),
        delete_application=OnCompleteAction.always(Return(from_administrator)),
        update_application=OnCompleteAction.always(Return(from_administrator)),
        opt_in=OnCompleteAction.always(Approve()),
        close_out=OnCompleteAction.always(Approve()),
        clear_state=OnCompleteAction.always(Approve()),
    ),
)


@router.method
def set_administrator(new_admin: abi.Address):
    return Seq(
        Assert(from_administrator),
        put_admin(new_admin.get()),
    )


@router.method
def set_policy(r_basis: abi.Uint64, r_recv: abi.Address):
    return Seq(
        Assert(from_administrator),
        (r_basis_stored := App.globalGetEx(Int(0), r_basis_key)),
        (r_recv_stored := App.globalGetEx(Int(0), r_recv_key)),
        Assert(Not(r_basis_stored.hasValue())),
        Assert(Not(r_recv_stored.hasValue())),
        Assert(r_basis.get() <= Int(basis_point_multiplier)),
        App.globalPut(r_basis_key, r_basis.get()),
        App.globalPut(r_recv_key, r_recv.get()),
    )


@router.method
def set_asset(asset: abi.Asset, is_allowed: abi.Bool):
    return Seq(
        Assert(from_administrator),
        bal := AssetHolding.balance(Global.current_application_address(), asset.get()),
        creator := AssetParam.creator(asset.get()),
        If(And(is_allowed.get(), Not(bal.hasValue())))
        .Then(
            # Opt in to asset
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.AssetTransfer,
                        TxnField.xfer_asset: asset.get(),
                        TxnField.asset_amount: Int(0),
                        TxnField.asset_receiver: Global.current_application_address(),
                    }
                ),
                InnerTxnBuilder.Submit(),
            ),
        )
        .ElseIf(And(Not(is_allowed.get()), bal.hasValue()))
        .Then(
            # Opt out, close asset to asset creator
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.AssetTransfer,
                        TxnField.xfer_asset: asset.get(),
                        TxnField.asset_amount: Int(0),
                        TxnField.asset_close_to: creator.value(),
                        TxnField.asset_receiver: creator.value(),
                    }
                ),
                InnerTxnBuilder.Submit(),
            ),
        ),
    )


@router.method
def transfer(
    asset: abi.Asset,
    amt: abi.Uint64,
    owner: abi.Account,
    buyer: abi.Account,
    royalty_acct: abi.Account,
    purchase_txn: abi.Transaction,
    purchase_asset: abi.Asset,
    offered_amt: abi.Uint64,
):
    # Get the auth_addr from local state of the owner
    # If its not present, a 0 is returned and the call fails when we try
    # to compare to the bytes of Txn.sender
    offer = App.localGet(owner.get(), Itob(asset.get()))
    offer_auth_addr = offered_auth(offer)
    offer_amt = offered_amount(offer)

    stored_royalty_recv = ScratchVar(TealType.bytes)
    stored_royalty_basis = ScratchVar(TealType.uint64)

    valid_transfer_group = And(
        Global.group_size() == Int(2),
        # App call sent by authorizing address
        Txn.sender() == offer_auth_addr,
        # No funny business
        purchase_txn.get().rekey_to() == Global.zero_address(),
        # payment txn should be from auth
        purchase_txn.get().sender() == offer_auth_addr,
        # transfer amount <= offered amount
        amt.get() <= offer_amt,
        # Passed the correct account according to the policy
        Or(
            And(
                purchase_txn.get().type_enum() == TxnType.AssetTransfer,
                purchase_txn.get().xfer_asset() == purchase_asset.get(),
                # Just to be sure
                purchase_txn.get().asset_close_to() == Global.zero_address(),
                # Make sure payments go to the right participants
                purchase_txn.get().asset_receiver()
                == Global.current_application_address(),
            ),
            And(
                purchase_txn.get().type_enum() == TxnType.Payment,
                # Just to be sure
                purchase_txn.get().close_remainder_to() == Global.zero_address(),
                # Make sure payments are going to the right participants
                purchase_txn.get().receiver() == Global.current_application_address(),
            ),
        ),
        royalty_acct.get() == stored_royalty_recv.load(),
    )

    return Seq(
        # Grab the royalty policy settings
        stored_royalty_recv.store(royalty_receiver()),
        stored_royalty_basis.store(royalty_basis()),
        # Make sure transactions look right
        Assert(valid_transfer_group),
        # Make royalty payment
        If(
            purchase_txn.get().type_enum() == TxnType.AssetTransfer,
            pay_assets(
                purchase_txn.get().xfer_asset(),
                purchase_txn.get().asset_amount(),
                owner.get(),
                royalty_acct.get(),
                stored_royalty_basis.load(),
            ),
            pay_algos(
                purchase_txn.get().amount(),
                owner.get(),
                royalty_acct.get(),
                stored_royalty_basis.load(),
            ),
        ),
        # Perform asset move
        move_asset(asset.get(), owner.get(), buyer.get(), amt.get()),
        # Clear listing from local state of owner
        update_offered(
            owner.get(),
            Itob(asset.get()),
            offer_auth_addr,
            offer_amt - amt.get(),
            Txn.sender(),
            offered_amt.get(),
        ),
    )


@router.method
def offer(
    asset: abi.Asset,
    amt: abi.Uint64,
    auth: abi.Address,
    prev_amt: abi.Uint64,
    prev_auth: abi.Address,
):
    return Seq(
        cb := AssetParam.clawback(asset.get()),
        bal := AssetHolding.balance(Txn.sender(), asset.get()),
        # Check that caller _has_ this asset
        Assert(bal.value() >= amt.get()),
        # Check that this app is the clawback for it
        Assert(And(cb.hasValue(), cb.value() == Global.current_application_address())),
        # Set the auth addr for this asset
        update_offered(
            Txn.sender(),
            Itob(asset.get()),
            auth.get(),
            amt.get(),
            prev_auth.get(),
            prev_amt.get(),
        ),
    )


@router.method
def royalty_free_move(
    asset: abi.Asset,
    amt: abi.Uint64,
    from_acct: abi.Account,
    to_acct: abi.Account,
    offered_amt: abi.Uint64,
):

    offer = App.localGet(from_acct.get(), Itob(asset.get()))

    return Seq(
        (curr_offer_amt := ScratchVar()).store(offered_amount(offer)),
        (curr_offer_auth := ScratchVar()).store(offered_auth(offer)),
        # Must match what is currently offered and amt to move is less than 
        # or equal to what has been offered
        Assert(curr_offer_amt.load() == offered_amt.get()),
        Assert(curr_offer_amt.load() >= amt.get()),
        Assert(curr_offer_auth.load() == Txn.sender()),
        # Delete the offer
        update_offered(
            from_acct.get(),
            Itob(asset.get()),
            Bytes(""),
            Int(0),
            curr_offer_auth.load(),
            curr_offer_amt.load(),
        ),
        # Move it
        move_asset(asset.get(), from_acct.get(), to_acct.get(), amt.get()),
    )


@router.method
def get_offer(asset_id: abi.Uint64, acct: abi.Account, *, output: Offer):
    return Seq(
        stored_offer := App.localGetEx(acct.get(), Int(0), Itob(asset_id.get())),
        Assert(stored_offer.hasValue()),
        output.decode(stored_offer.value()),
    )


@router.method
def get_policy(*, output: Policy):
    return Seq(
        (addr := abi.Address()).decode(royalty_receiver()),
        (amt := abi.Uint64()).set(royalty_basis()),
        output.set(addr, amt),
    )


@router.method
def get_administrator(*, output: abi.Address):
    return output.decode(get_admin())


approval, clear, contract = router.compile_program(
    version=6,
    optimize=OptimizeOptions(scratch_slots=True),
)


def get_approval():
    return approval


def get_clear():
    return clear

def get_contract():
    return contract

if __name__ == "__main__":
    import json

    with open("abi.json", "w") as f:
        f.write(json.dumps(contract.dictify(), indent=4))

    with open("approval.teal", "w") as f:
        f.write(approval)

    with open("clear.teal", "w") as f:
        f.write(clear)
