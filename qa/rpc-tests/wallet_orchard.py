#!/usr/bin/env python3
# Copyright (c) 2022 The Zcash developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    NU5_BRANCH_ID,
    assert_equal,
    connect_nodes_bi,
    get_coinbase_address,
    nuparams,
    start_nodes,
    stop_nodes,
    sync_blocks,
    wait_and_assert_operationid_status,
    wait_bitcoinds,
)

from decimal import Decimal

# Test wallet behaviour with the Orchard protocol
class WalletOrchardTest(BitcoinTestFramework):
    def __init__(self):
        super().__init__()
        self.num_nodes = 4

    def setup_nodes(self):
        return start_nodes(self.num_nodes, self.options.tmpdir, [[
            '-experimentalfeatures',
            '-orchardwallet',
            nuparams(NU5_BRANCH_ID, 210),
        ]] * self.num_nodes)

    def run_test(self):
        # Sanity-check the test harness
        assert_equal(self.nodes[0].getblockcount(), 200)

        # Get a new orchard-only unified address
        acct1 = self.nodes[1].z_getnewaccount()['account']
        addrRes1 = self.nodes[1].z_getaddressforaccount(acct1, ['orchard'])
        assert_equal(acct1, addrRes1['account'])
        assert_equal(addrRes1['pools'], ['orchard'])
        ua1 = addrRes1['unifiedaddress']

        # Verify that we have only an Orchard component
        receiver_types = self.nodes[0].z_listunifiedreceivers(ua1)
        assert_equal(set(['orchard']), set(receiver_types))

        # Verify balance
        assert_equal({'pools': {}, 'minimum_confirmations': 1}, self.nodes[1].z_getbalanceforaccount(acct1))

        # Send some sapling funds to node 2 for later spending after we split the network
        acct2 = self.nodes[2].z_getnewaccount()['account']
        addrRes2 = self.nodes[2].z_getaddressforaccount(acct2, ['sapling', 'orchard'])
        assert_equal(acct2, addrRes2['account'])
        ua2 = addrRes2['unifiedaddress']
        saplingAddr2 = self.nodes[2].z_listunifiedreceivers(ua2)['sapling']

        recipients = [{"address": saplingAddr2, "amount": Decimal('10')}]
        myopid = self.nodes[0].z_sendmany(get_coinbase_address(self.nodes[0]), recipients, 1, 0)
        wait_and_assert_operationid_status(self.nodes[0], myopid)

        # Mine the tx & activate NU5
        self.sync_all()
        self.nodes[0].generate(10)
        self.sync_all()

        # Check the value sent to saplingAddr2 was received in node 2's account
        assert_equal(
                {'pools': {'sapling': {'valueZat': Decimal('1000000000')}}, 'minimum_confirmations': 1}, 
                self.nodes[2].z_getbalanceforaccount(acct2))

        # Node 0 shields some funds
        # t-coinbase -> Orchard
        recipients = [{"address": ua1, "amount": Decimal('10')}]
        myopid = self.nodes[0].z_sendmany(get_coinbase_address(self.nodes[0]), recipients, 1, 0)
        wait_and_assert_operationid_status(self.nodes[0], myopid)

        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()

        assert_equal(
                {'pools': {'orchard': {'valueZat': Decimal('1000000000')}}, 'minimum_confirmations': 1}, 
                self.nodes[1].z_getbalanceforaccount(acct1))

        # Split the network
        self.split_network()

        # Send another tx to ua1
        recipients = [{"address": ua1, "amount": Decimal('10')}]
        myopid = self.nodes[0].z_sendmany(get_coinbase_address(self.nodes[0]), recipients, 1, 0)
        wait_and_assert_operationid_status(self.nodes[0], myopid)

        # Mine the tx & generate a majority chain on the 0/1 side of the split
        self.sync_all()
        self.nodes[0].generate(10)
        self.sync_all()

        assert_equal(
                {'pools': {'orchard': {'valueZat': Decimal('2000000000')}}, 'minimum_confirmations': 1}, 
                self.nodes[1].z_getbalanceforaccount(acct1))

        # On the other side of the split, send some funds to node 3
        acct3 = self.nodes[3].z_getnewaccount()['account']
        addrRes3 = self.nodes[3].z_getaddressforaccount(acct3, ['sapling', 'orchard'])
        assert_equal(acct3, addrRes3['account'])
        ua3 = addrRes3['unifiedaddress']

        recipients = [{"address": ua3, "amount": Decimal('1')}]
        myopid = self.nodes[2].z_sendmany(ua2, recipients, 1, 0, True)
        rollback_tx = wait_and_assert_operationid_status(self.nodes[2], myopid)

        self.sync_all()
        self.nodes[2].generate(1)
        self.sync_all()

        assert_equal(
                {'pools': {'sapling': {'valueZat': Decimal('900000000')}}, 'minimum_confirmations': 1}, 
                self.nodes[2].z_getbalanceforaccount(acct2))

        assert_equal(
                {'pools': {'orchard': {'valueZat': Decimal('100000000')}}, 'minimum_confirmations': 1}, 
                self.nodes[3].z_getbalanceforaccount(acct3))

        # Check that the mempools are empty
        for i in range(self.num_nodes):
            assert_equal(set([]), set(self.nodes[i].getrawmempool()))

        # Reconnect the nodes; nodes 2 and 3 will re-org to node 0's chain.
        print("Re-joining the network so that nodes 2 and 3 reorg")
        # We can't use `self.join_network()` because the the nodes's mempools fail to synchronize on restart
        assert self.is_network_split
        stop_nodes(self.nodes)
        wait_bitcoinds()
        self.nodes = self.setup_nodes()
        connect_nodes_bi(self.nodes, 1, 2)
        sync_blocks(self.nodes[1:3])
        connect_nodes_bi(self.nodes, 0, 1)
        connect_nodes_bi(self.nodes, 2, 3)
        self.is_network_split = False
        sync_blocks(self.nodes)

        # split 0/1's chain should have won, so their wallet balance should be consistent
        assert_equal(
                {'pools': {'orchard': {'valueZat': Decimal('2000000000')}}, 'minimum_confirmations': 1}, 
                self.nodes[1].z_getbalanceforaccount(acct1))

        # split 2/3's chain should have been rolled back, so their txn should have been
        # un-mined and returned to the mempool
        assert_equal(set([rollback_tx]), set(self.nodes[2].getrawmempool()))

        # our sole Sapling note is spent, so our confirmed balance is currently 0
        assert_equal(
                {'pools': {}, 'minimum_confirmations': 1}, 
                self.nodes[2].z_getbalanceforaccount(acct2))

        # our incoming change (unconfirmed, still in the mempool) is 9 zec
        assert_equal(
                {'pools': {'sapling': {'valueZat': Decimal('900000000')}}, 'minimum_confirmations': 0}, 
                self.nodes[2].z_getbalanceforaccount(acct2, 0))

        # the transaction was un-mined, so we have no confirmed balance
        assert_equal(
                {'pools': {}, 'minimum_confirmations': 1}, 
                self.nodes[3].z_getbalanceforaccount(acct3))

        # our unconfirmed balance is 1 zec
        assert_equal(
                {'pools': {'orchard': {'valueZat': Decimal('100000000')}}, 'minimum_confirmations': 0}, 
                self.nodes[3].z_getbalanceforaccount(acct3, 0))

if __name__ == '__main__':
    WalletOrchardTest().main()