from pyteal import *


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


addr = abi.Address()
share = abi.Uint64()
participant = abi.Tuple(addr, share)
share_policy = abi.DynamicArray(participant)

asset_id = abi.Uint8()
asset_policy = abi.DynamicArray(asset_id)

royalty_policy = abi.Tuple(share_policy, asset_policy)

set_policy_selector = MethodSignature("set_policy(asset,{})void".format(royalty_policy))


@Subroutine(TealType.uint64)
def set_policy():
    asset_id = Txn.assets[Btoi(Txn.application_args[1])]
    return Seq(
        # Just stuff the whole thing in for now, eventually will need more space for this (>120 bytes)
        App.globalPut(Itob(asset_id), Txn.application_args[2]),
        Int(1),
    )


transfer_selector = MethodSignature("transfer(asset,address,address,txn,uint8[])void")

app_refs = abi.DynamicArray(abi.Uint8())


@Subroutine(TealType.uint64)
def transfer():
    asset_ref = Btoi(Txn.application_args[1])
    sender = Txn.application_args[2]
    receiver = Txn.application_args[3]
    payment_ref = Btoi(Txn.application_args[4])
    extra_app_refs = app_refs.decode(Txn.application_args[5])

    _policy = royalty_policy.new_instance()
    _share_policy = share_policy.new_instance()
    return Seq(
        _policy.decode(App.globalGet(Itob(Txn.assets[asset_ref]))),
        _policy[0].store_into(_share_policy),
        Int(1)
    )



@Subroutine(TealType.uint64)
def move():
    return Int(1)


def approval():
    from_creator = Txn.sender() == Global.creator_address()

    action_router = Cond(
        [And(Txn.application_args[0] == Bytes("create"), from_creator), create_nft()],
        [
            And(Txn.application_args[0] == set_policy_selector, from_creator),
            set_policy(),
        ],
        [And(Txn.application_args[0] == Bytes("move"), from_creator), move()],
        [Txn.application_args[0] == Bytes("transfer"), transfer()],
    )

    return Cond(
        [Txn.application_id() == Int(0), Approve()],
        [Txn.on_completion() == OnComplete.DeleteApplication, Reject()],
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
