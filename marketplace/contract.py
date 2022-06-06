from pyteal import *


app_key = Bytes("app")
asset_key = Bytes("asset")
price_key = Bytes("price")
amount_key = Bytes("amount")
account_key = Bytes("account")


from_creator = Txn.sender() == Global.creator_address()
router = Router(
    "arc-18-marketplace",
    BareCallActions(
        no_op=OnCompleteAction.create_only(Approve()),
        delete_application=OnCompleteAction.always(Return(from_creator)),
        update_application=OnCompleteAction.always(Return(from_creator)),
        opt_in=OnCompleteAction.always(Approve()),
        close_out=OnCompleteAction.always(Approve()),
        clear_state=OnCompleteAction.always(Approve()),
    ),
)


@router.method
def list(
    asset: abi.Asset,
    app: abi.Application,
    amt: abi.Uint64,
    price: abi.Uint64,
    offer_txn: abi.ApplicationCallTransaction,
):
    return Seq(
        asset_balance := AssetHolding.balance(Txn.sender(), asset.get()),
        asset_freeze := AssetParam.freeze(asset.get()),
        asset_clawback := AssetParam.clawback(asset.get()),
        app_addr := AppParam.address(app.get()),
        offered := App.localGetEx(Txn.sender(), app.get(), Itob(asset.deref())),
        # Check stuff
        Assert(App.globalGet(app_key) == Int(0)),  # We don't have anything there yet
        Assert(
            offer_txn.get().application_id() == app.deref()
        ),  # The app call to trigger offered is present and same as app id
        Assert(asset_balance.value() > Int(0)),  # The caller has the asset
        Assert(
            asset_freeze.value() == app_addr.value()
        ),  # The freeze/clawback are set to app addr
        Assert(
            asset_clawback.value() == app_addr.value()
        ),  # The authorized addr for this asset is this apps account
        Assert(offered_auth(offered.value()) == Global.current_application_address()),
        Assert(offered_amount(offered.value()) >= amt.get()),
        # Set appropriate parameters
        App.globalPut(app_key, app.deref()),
        App.globalPut(asset_key, asset.deref()),
        App.globalPut(amount_key, amt.get()),
        App.globalPut(price_key, price.get()),
        App.globalPut(account_key, Txn.sender()),
    )


@router.method
def buy(
    asset: abi.Asset,
    app: abi.Application,
    app_acct: abi.Account,
    owner: abi.Account,
    royalty_acct: abi.Account,
    amt: abi.Uint64,
    pay_txn: abi.PaymentTransaction,
):
    # Make sure its the asset for sale
    # Make sure the payment is for the right amount
    # Issue inner app call to royalty to move asset
    return Seq(
        current_offer := App.localGetEx(owner.get(), app.get(), Itob(asset.deref())),
        # Matches what we have in global state
        Assert(current_offer.hasValue()),
        Assert(owner.deref() == App.globalGet(account_key)),
        Assert(app.deref() == App.globalGet(app_key)),
        Assert(asset.deref() == App.globalGet(asset_key)),
        Assert(pay_txn.get().amount() >= App.globalGet(price_key)),
        Assert(pay_txn.get().receiver() == Global.current_application_address()),
        Assert(amt.get() <= App.globalGet(amount_key)),
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.Payment,
                TxnField.amount: pay_txn.get().amount(),
                TxnField.receiver: app_acct.deref(),
            }
        ),
        InnerTxnBuilder.Next(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.ApplicationCall,
                TxnField.application_id: app.deref(),
                TxnField.application_args: [
                    MethodSignature(
                        "transfer(asset,uint64,account,account,account,txn,asset,uint64)void"
                    ),
                    Itob(Int(0)),  # Royalty Asset in 0th position of asset array
                    Itob(amt.get()),  # The number of units being purchased
                    Suffix(
                        Itob(Int(1)), Int(7)
                    ),  # Current owner first element here but offset by 1
                    Suffix(Itob(Int(2)), Int(7)),  # Buyer
                    Suffix(Itob(Int(3)), Int(7)),  # Who we need to pay for royalties
                    Suffix(Itob(Int(0)), Int(7)),  # Asset idx of 0, should be ignored
                    Itob(
                        offered_amount(current_offer.value())
                    ),  # Current offered amount
                ],
                TxnField.assets: [asset.deref()],
                TxnField.accounts: [owner.deref(), Txn.sender(), royalty_acct.deref()],
            }
        ),
        InnerTxnBuilder.Submit(),
        # Wipe listing
        App.globalDel(asset_key),
        App.globalDel(amount_key),
        App.globalDel(app_key),
        App.globalDel(account_key),
        App.globalDel(price_key),
    )


@Subroutine(TealType.uint64)
def offered_amount(offer):
    return ExtractUint64(offer, Int(32))


@Subroutine(TealType.bytes)
def offered_auth(offer):
    return Extract(offer, Int(0), Int(32))


approval, clear, contract = router.compile_program(
    version=6, optimize=OptimizeOptions(scratch_slots=True)
)


def get_approval():
    return approval


def get_clear():
    return clear

def get_contract():
    return contract



if __name__ == "__main__":
    print(get_approval())
