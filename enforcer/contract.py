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
def get_royalty_receiver():
    return App.globalGet(r_recv_key)


@Subroutine(TealType.uint64)
def get_royalty_basis():
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
            Seq(
                Assert(offered_amount(previous.value()) == prev_amt),
                Assert(offered_auth(previous.value()) == prev_auth),
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
        clear_state=OnCompleteAction.call_only(Approve()),
    ),
)


@router.method
def set_administrator(new_admin: abi.Address):
    """Sets the administrator for this royalty enforcer"""
    return Seq(
        Assert(from_administrator),
        put_admin(new_admin.get()),
    )


@router.method
def set_policy(royalty_basis: abi.Uint64, royalty_receiver: abi.Address):
    """Sets the royalty basis and royalty receiver for this royalty enforcer"""
    return Seq(
        Assert(from_administrator),
        (r_basis_stored := App.globalGetEx(Int(0), r_basis_key)),
        (r_recv_stored := App.globalGetEx(Int(0), r_recv_key)),
        Assert(Not(r_basis_stored.hasValue())),
        Assert(Not(r_recv_stored.hasValue())),
        Assert(royalty_basis.get() <= Int(basis_point_multiplier)),
        App.globalPut(r_basis_key, royalty_basis.get()),
        App.globalPut(r_recv_key, royalty_receiver.get()),
    )


@router.method
def set_payment_asset(payment_asset: abi.Asset, is_allowed: abi.Bool):
    """Triggers the contract account to opt in or out of an asset that may be used for payment of royalties"""
    return Seq(
        Assert(from_administrator),
        bal := AssetHolding.balance(
            Global.current_application_address(), payment_asset.asset_id()
        ),
        creator := AssetParam.creator(payment_asset.asset_id()),
        If(And(is_allowed.get(), Not(bal.hasValue())))
        .Then(
            # Opt in to asset
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.type_enum: TxnType.AssetTransfer,
                        TxnField.xfer_asset: payment_asset.asset_id(),
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
                        TxnField.xfer_asset: payment_asset.asset_id(),
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
    royalty_asset: abi.Asset,
    royalty_asset_amount: abi.Uint64,
    owner: abi.Account,
    buyer: abi.Account,
    royalty_receiver: abi.Account,
    payment_txn: abi.PaymentTransaction,
    offered_amt: abi.Uint64,
):
    """Transfers an Asset from one account to another and enforces royalty payments. 
        This instance of the `transfer` method requires a PaymentTransaction for payment in algos
    """

    # Get the auth_addr from local state of the owner
    # If its not present, a 0 is returned and the call fails when we try
    # to compare to the bytes of Txn.sender
    offer = App.localGet(owner.address(), Itob(royalty_asset.asset_id()))
    offer_auth_addr = offered_auth(offer)
    offer_amt = offered_amount(offer)

    stored_royalty_recv = ScratchVar(TealType.bytes)
    stored_royalty_basis = ScratchVar(TealType.uint64)

    valid_transfer_group = Seq(
        Assert(Global.group_size() == Int(2)),
        # App call sent by authorizing address
        Assert(Txn.sender() == offer_auth_addr),
        # No funny business
        Assert(payment_txn.get().rekey_to() == Global.zero_address()),
        # payment txn should be from auth
        Assert(payment_txn.get().sender() == offer_auth_addr),
        # transfer amount <= offered amount
        Assert(royalty_asset_amount.get() <= offer_amt),
        # Passed the correct account according to the policy
        Assert(payment_txn.get().type_enum() == TxnType.Payment),
        # Just to be sure
        Assert(payment_txn.get().close_remainder_to() == Global.zero_address()),
        # Make sure payments are going to the right participants
        Assert(payment_txn.get().receiver() == Global.current_application_address()),
        Assert(royalty_receiver.address() == stored_royalty_recv.load()),
    )

    return Seq(
        # Grab the royalty policy settings
        stored_royalty_recv.store(get_royalty_receiver()),
        stored_royalty_basis.store(get_royalty_basis()),
        # Make sure transactions look right
        valid_transfer_group,
        # Make royalty payment
        pay_algos(
            payment_txn.get().amount(),
            owner.address(),
            royalty_receiver.address(),
            stored_royalty_basis.load(),
        ),
        # Perform asset move
        move_asset(
            royalty_asset.asset_id(),
            owner.address(),
            buyer.address(),
            royalty_asset_amount.get(),
        ),
        # Clear listing from local state of owner
        update_offered(
            owner.address(),
            Itob(royalty_asset.asset_id()),
            offer_auth_addr,
            offer_amt - royalty_asset_amount.get(),
            Txn.sender(),
            offered_amt.get(),
        ),
    )


@router.method
def transfer(
    royalty_asset: abi.Asset,
    royalty_asset_amount: abi.Uint64,
    owner: abi.Account,
    buyer: abi.Account,
    royalty_receiver: abi.Account,
    payment_txn: abi.AssetTransferTransaction,
    payment_asset: abi.Asset,
    offered_amt: abi.Uint64,
):
    """Transfers an Asset from one account to another and enforces royalty payments.
        This instance of the `transfer` method requires an AssetTransfer transaction and an Asset to be passed 
        corresponding to the Asset id of the transfer transaction."""

    # Get the auth_addr from local state of the owner
    # If its not present, a 0 is returned and the call fails when we try
    # to compare to the bytes of Txn.sender
    offer = App.localGet(owner.address(), Itob(royalty_asset.asset_id()))
    offer_auth_addr = offered_auth(offer)
    offer_amt = offered_amount(offer)

    stored_royalty_recv = ScratchVar(TealType.bytes)
    stored_royalty_basis = ScratchVar(TealType.uint64)

    valid_transfer_group = And(
        Global.group_size() == Int(2),
        # App call sent by authorizing address
        Txn.sender() == offer_auth_addr,
        # No funny business
        payment_txn.get().rekey_to() == Global.zero_address(),
        # payment txn should be from auth
        payment_txn.get().sender() == offer_auth_addr,
        # transfer amount <= offered amount
        royalty_asset_amount.get() <= offer_amt,
        # Passed the correct account according to the policy
        payment_txn.get().type_enum() == TxnType.AssetTransfer,
        payment_txn.get().xfer_asset() == payment_asset.asset_id(),
        # Just to be sure
        payment_txn.get().asset_close_to() == Global.zero_address(),
        # Make sure payments go to the right participants
        payment_txn.get().asset_receiver() == Global.current_application_address(),
        royalty_receiver.address() == stored_royalty_recv.load(),
    )

    return Seq(
        # Grab the royalty policy settings
        stored_royalty_recv.store(get_royalty_receiver()),
        stored_royalty_basis.store(get_royalty_basis()),
        # Make sure transactions look right
        Assert(valid_transfer_group),
        pay_assets(
            payment_txn.get().xfer_asset(),
            payment_txn.get().asset_amount(),
            owner.address(),
            royalty_receiver.address(),
            stored_royalty_basis.load(),
        ),
        # Perform asset move
        move_asset(
            royalty_asset.asset_id(),
            owner.address(),
            buyer.address(),
            royalty_asset_amount.get(),
        ),
        # Clear listing from local state of owner
        update_offered(
            owner.address(),
            Itob(royalty_asset.asset_id()),
            offer_auth_addr,
            offer_amt - royalty_asset_amount.get(),
            Txn.sender(),
            offered_amt.get(),
        ),
    )


@router.method
def offer(
    royalty_asset: abi.Asset,
    royalty_asset_amount: abi.Uint64,
    auth_address: abi.Address,
    prev_offer_amt: abi.Uint64,
    prev_offer_auth: abi.Address,
):
    """Flags that an asset is offered for sale and sets address authorized to submit the transfer"""
    return Seq(
        cb := AssetParam.clawback(royalty_asset.asset_id()),
        bal := AssetHolding.balance(Txn.sender(), royalty_asset.asset_id()),
        # Check that caller _has_ this asset
        Assert(bal.value() >= royalty_asset_amount.get()),
        # Check that this app is the clawback for it
        Assert(And(cb.hasValue(), cb.value() == Global.current_application_address())),
        # Set the auth addr for this asset
        update_offered(
            Txn.sender(),
            Itob(royalty_asset.asset_id()),
            auth_address.get(),
            royalty_asset_amount.get(),
            prev_offer_auth.get(),
            prev_offer_amt.get(),
        ),
    )


@router.method
def royalty_free_move(
    royalty_asset: abi.Asset,
    royalty_asset_amount: abi.Uint64,
    owner: abi.Account,
    receiver: abi.Account,
    offered_amt: abi.Uint64,
):
    """Moves the asset passed from one account to another"""

    offer = App.localGet(owner.address(), Itob(royalty_asset.asset_id()))

    return Seq(
        (curr_offer_amt := ScratchVar()).store(offered_amount(offer)),
        (curr_offer_auth := ScratchVar()).store(offered_auth(offer)),
        # Must match what is currently offered and amt to move is less than
        # or equal to what has been offered
        Assert(curr_offer_amt.load() == offered_amt.get()),
        Assert(curr_offer_amt.load() >= royalty_asset_amount.get()),
        Assert(curr_offer_auth.load() == Txn.sender()),
        # Delete the offer
        update_offered(
            owner.address(),
            Itob(royalty_asset.asset_id()),
            Bytes(""),
            Int(0),
            curr_offer_auth.load(),
            curr_offer_amt.load(),
        ),
        # Move it
        move_asset(
            royalty_asset.asset_id(),
            owner.address(),
            receiver.address(),
            royalty_asset_amount.get(),
        ),
    )


@router.method
def get_offer(royalty_asset: abi.Uint64, owner: abi.Account, *, output: Offer):
    return Seq(
        stored_offer := App.localGetEx(
            owner.address(), Int(0), Itob(royalty_asset.get())
        ),
        Assert(stored_offer.hasValue()),
        output.decode(stored_offer.value()),
    )


@router.method
def get_policy(*, output: Policy):
    return Seq(
        (addr := abi.Address()).decode(get_royalty_receiver()),
        (amt := abi.Uint64()).set(get_royalty_basis()),
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
