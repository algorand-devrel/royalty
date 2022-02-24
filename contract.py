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


addr = abi.Byte()
share = abi.Uint64()
participant = abi.Tuple(addr, share)

policy = abi.DynamicArray(participant)

set_policy_selector = MethodSignature(
    "set_policy(asset,(address,uint64)[],uint64[])void"
)


@Subroutine(TealType.uint64)
def set_policy():
    _participant = participant.new_instance()
    _share = share.new_instance()
    _addr = addr.new_instance()

    return Seq(
        policy.decode(Txn.application_args[1]),
        policy[0].store_into(_participant),
        _participant[0].store_into(_addr),
        _participant[1].store_into(_share),
        App.globalPut(Itob(_addr.get()), _share.get()),
        Int(1),
    )


@Subroutine(TealType.uint64)
def transfer():
    return Int(1)


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
