from pyteal import *


asset_key = Bytes("royalty_asset")
recv_key = Bytes("royalty_receiver")
share_key = Bytes("royalty_share")
allowed_key = Bytes("allowed_assets")


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
        Int(1),
    )


set_policy_selector = MethodSignature("set_policy(uint64,address,uint64,uint64[])void")


@Subroutine(TealType.uint64)
def set_policy():
    asset_id = Txn.application_args[1]
    recv = Txn.application_args[2]
    share = Txn.application_args[3]
    assets = Txn.application_args[4]
    return Seq(
        App.globalPut(asset_key, asset_id),
        App.globalPut(recv_key, recv),
        App.globalPut(share_key, share),
        App.globalPut(allowed_key, assets),
        Int(1),
    )


transfer_selector = MethodSignature("transfer(asset,account,account,account,txn)void")


@Subroutine(TealType.uint64)
def transfer():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    owner_acct = Txn.accounts[Btoi(Txn.application_args[2])]
    buyer_acct = Txn.accounts[Btoi(Txn.application_args[3])]
    royalty_acct = Txn.accounts[Btoi(Txn.application_args[4])]
    payment_txn = Gtxn[Txn.group_index() - Int(1)]

    return Seq(
        # TODO: add validation checks to make sure it all looks right
        # sender should be current owner
        # payment should be from buyer
        # no funny biz with closes and rekey'd accts not allowed?
        move_asset(asset_id, owner_acct, buyer_acct),
        If(
            payment_txn.type_enum() == TxnType.Payment,
            make_algo_payment(
                royalty_acct,
                get_share_amount(payment_txn.amount(), Btoi(App.globalGet(share_key))),
            ),
            make_asset_payment(
                royalty_acct,
                payment_txn.xfer_asset(),
                get_share_amount(
                    payment_txn.asset_amount(), Btoi(App.globalGet(share_key))
                ),
            ),
        ),
        Int(1),
    )


@Subroutine(TealType.none)
def move_asset(asset_id, owner, buyer):
    return Seq(
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: asset_id,
                TxnField.asset_amount: Int(1),
                TxnField.asset_sender: owner,
                TxnField.asset_receiver: buyer,
            }
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.uint64)
def get_share_amount(total, share):
    return WideRatio([total], [share, Int(10000)])


@Subroutine(TealType.none)
def make_algo_payment(to, amount):
    return Seq(
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.Payment,
                TxnField.receiver: to,
                TxnField.amount: amount,
            }
        ),
        InnerTxnBuilder.Submit(),
    )


@Subroutine(TealType.none)
def make_asset_payment(to, asa_id, amount):
    return Seq(
        InnerTxnBuilder.Begin(),
        InnerTxnBuilder.SetFields(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: asa_id,
                TxnField.asset_receiver: to,
                TxnField.asset_amount: amount,
            }
        ),
        InnerTxnBuilder.Submit(),
    )


def approval():
    from_creator = Txn.sender() == Global.creator_address()

    action_router = Cond(
        [And(Txn.application_args[0] == Bytes("create"), from_creator), create_nft()],
        [
            And(Txn.application_args[0] == set_policy_selector, from_creator),
            set_policy(),
        ],
        [Txn.application_args[0] == transfer_selector, transfer()],
    )

    return Cond(
        [Txn.application_id() == Int(0), Approve()],
        [Txn.on_completion() == OnComplete.DeleteApplication, Return(from_creator)],
        [Txn.on_completion() == OnComplete.UpdateApplication, Return(from_creator)],
        [Txn.on_completion() == OnComplete.OptIn, Reject()],
        [Txn.on_completion() == OnComplete.CloseOut, Reject()],
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
