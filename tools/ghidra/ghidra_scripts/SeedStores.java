// Seed the function that installs a class table, where the analyser left the store in no function.
//
// `tools/class-ctors.py` measures every `C7 /0` store of one of the 492 class tables in .text and
// attributes it to the function that contains it. A store outside every function is evidence of a
// function the analyser never created -- and unlike an assert site it *names its own class*, because
// the immediate is the class's table. Thirty-one of the 1,036 stores sat that way; nine classes had no
// constructor name at all because theirs was one of them.
//
// The instrument is SeedGaps' fourth pass, pointed at a different kind of evidence. A candidate entry
// is the byte after a 0xCC fill run before the store, nearest first. It is kept only when three things
// are measured, not guessed: the body created there contains the store; the instruction covering the
// store is a MOV with a scalar operand equal to the table the row names; and the body ends at a
// terminator. A candidate inside a function the map already has is never tried, and a candidate with
// another function between it and the store is ruled out (fillStarts' tests, reused).
//
// A store that keeps none of its candidates is written to the CSV with the test it failed, so "8 left"
// is a measurement rather than a silence.
//
// No name is invented here either: the seeded entries carry the addresses, and `tools/class-ctors.py`
// decides the `ctor_<Class>` / `dtor_<Class>` name from the shapes it already measures.
//
// usage: SeedStores.java <class-ctors --orphans-out TSV> [maxBackBytes] [csvOut]
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.FlowType;
import java.io.*;
import java.util.*;

public class SeedStores extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: SeedStores.java <orphans.tsv> [maxBackBytes] [csvOut]");
            return;
        }
        int maxBack = args.length > 1 ? Integer.decode(args[1]) : 0x1000;
        String csvPath = args.length > 2 ? args[2] : null;

        List<long[]> evidence = new ArrayList<>();      // {store_va, table_va}
        List<String> classes = new ArrayList<>();
        File f = new File(args[0]);
        if (!f.isFile()) {
            println("no orphan-store list at " + args[0] + "; nothing to seed");
            return;
        }
        BufferedReader reader = new BufferedReader(new FileReader(f));
        String line = reader.readLine();                // header
        while ((line = reader.readLine()) != null) {
            if (line.trim().isEmpty()) continue;
            String[] p = line.split("[,\t]", -1);
            if (p.length < 3) continue;
            evidence.add(new long[] { Long.decode(p[0]), Long.decode(p[1]) });
            classes.add(p.length > 2 ? p[2] : "");
        }
        reader.close();

        int before = countMissing(evidence);
        println("orphan stores: " + evidence.size() + ", in no function: " + before);

        StringBuilder rows = new StringBuilder("function_va,store_va,table_va,class,kept_by,size,reason\n");
        Map<String, Integer> why = new TreeMap<>();
        int seeded = 0;
        for (int i = 0; i < evidence.size(); i++) {
            long store = evidence.get(i)[0], table = evidence.get(i)[1];
            if (getFunctionContaining(toAddr(store)) != null) continue;
            String keptBy = null;
            String fail = null;
            long entry = 0;
            int size = 0;
            List<Long> cands = fillStarts(store, maxBack, 8);
            if (cands.isEmpty()) fail = "no 0xCC boundary before the store in this section";
            for (Long cand : cands) {
                Address at = toAddr(cand);
                if (getFunctionAt(at) != null) {
                    fail = "the boundary byte is already a function entry";
                    continue;
                }
                disassemble(at);
                Function fn = createFunction(at, null);
                if (fn == null) {
                    fail = "no body decodes from the boundary byte";
                    continue;
                }
                boolean has = fn.getBody().contains(toAddr(store));
                boolean mov = has && storesTable(store, table);
                boolean ends = mov && endsInTerminator(fn);
                if (has && mov && ends) {
                    keptBy = "entry after a 0xCC fill run";
                    entry = cand;
                    size = (int) fn.getBody().getNumAddresses();
                    break;
                }
                fail = !has ? "the body from the nearest boundary ends before the store"
                        : !mov ? "the instruction at the store is not a MOV of the table from that entry"
                               : "the body does not end at a terminator";
                tryRemove(fn);
            }
            if (keptBy == null && fail == null) fail = "no candidate kept the store";
            if (keptBy == null) {
                // Same fallback SeedGaps uses for a site whose entry is reached only through a jump:
                // promote the head of the block the store sits in, and record that it is a block.
                Long head = blockHead(store, maxBack);
                if (head != null) {
                    Address at = toAddr(head);
                    if (getFunctionAt(at) == null && getFunctionContaining(at) == null) {
                        disassemble(at);
                        Function fn = createFunction(at, null);
                        if (fn != null && fn.getBody().contains(toAddr(store)) && storesTable(store, table)
                                && endsInTerminator(fn)) {
                            keptBy = "promoted block head (not a function entry)";
                            entry = head;
                            size = (int) fn.getBody().getNumAddresses();
                        } else if (fn != null) {
                            tryRemove(fn);
                        }
                    }
                }
            }
            if (keptBy == null) {
                why.put(fail, why.getOrDefault(fail, 0) + 1);
                rows.append(String.format(",,0x%08x,%s,rejected,0,%s%n", table, classes.get(i), fail));
                continue;
            }
            seeded++;
            rows.append(String.format("0x%08x,0x%08x,0x%08x,%s,%s,%d,%n",
                    entry, store, table, classes.get(i), keptBy, size));
        }

        if (seeded > 0) analyzeChanges(currentProgram);
        println("seeded " + seeded + " of " + evidence.size() + " orphan stores"
                + (csvPath != null ? " (list: " + csvPath + ")" : ""));
        for (Map.Entry<String, Integer> e : why.entrySet()) {
            println("  rejected " + e.getValue() + ": " + e.getKey());
        }
        println("orphan stores in no function: " + before + " -> " + countMissing(evidence));
        println("functions now: " + currentProgram.getFunctionManager().getFunctionCount());
        if (csvPath != null) {
            try (PrintWriter w = new PrintWriter(new FileWriter(csvPath))) {
                w.print(rows);
            }
        }
    }

    /** The instruction covering {@code store} is a MOV whose immediate operand is {@code table}. */
    private boolean storesTable(long store, long table) {
        Instruction ins = getInstructionContaining(toAddr(store));
        if (ins == null || ins.getAddress().getOffset() != store) return false;
        if (!ins.getMnemonicString().toUpperCase().startsWith("MOV")) return false;
        // A stack slot is a local, not an object's vtable: the operand must not be EBP/ESP relative.
        for (Object o : ins.getOpObjects(0)) {
            if (o instanceof Register) {
                String n = ((Register) o).getName().toUpperCase();
                if (n.equals("EBP") || n.equals("ESP")) return false;
            }
        }
        for (int op = 0; op < ins.getNumOperands(); op++) {
            for (Object o : ins.getOpObjects(op)) {
                if (o instanceof Scalar && ((Scalar) o).getUnsignedValue() == table) return true;
            }
        }
        return false;
    }

    private int countMissing(List<long[]> evidence) {
        int missing = 0;
        for (long[] row : evidence) if (getFunctionContaining(toAddr(row[0])) == null) missing++;
        return missing;
    }

    private boolean tryRemove(Function fn) {
        Address entry = fn.getEntryPoint();
        try {
            removeFunction(fn);
        } catch (Exception e) {
            return false;
        }
        return getFunctionAt(entry) == null;
    }

    /** True when the last instruction of the body returns, jumps or stops. */
    private boolean endsInTerminator(Function fn) {
        Instruction ins = getInstructionAt(fn.getBody().getMaxAddress());
        if (ins == null) return false;
        String m = ins.getMnemonicString().toUpperCase();
        return m.startsWith("RET") || m.equals("JMP") || m.equals("HLT") || ins.getFlowType().isTerminal();
    }

    /** First instruction of the block containing {@code va}, after disassembling the gap before it. */
    private Long blockHead(long va, int maxBack) {
        Memory mem = currentProgram.getMemory();
        MemoryBlock block = mem.getBlock(toAddr(va));
        if (block == null) return null;
        long lo = Math.max(block.getStart().getOffset(), va - maxBack);
        Address from = toAddr(lo);
        Function previous = getFunctionContaining(from);
        if (previous != null) from = previous.getBody().getMaxAddress().add(1);
        disassemble(from);
        Instruction ins = getInstructionContaining(toAddr(va));
        if (ins == null) return null;
        Address cur = ins.getAddress();
        for (int guard = 0; guard < 4096; guard++) {
            Instruction prev = getInstructionBefore(cur);
            if (prev == null) break;
            if (!prev.getAddress().add(prev.getLength()).equals(cur)) break;   // a gap starts a block
            FlowType ft = prev.getFlowType();
            if (ft.isTerminal() || ft.isJump() || ft.isCall()) break;
            cur = prev.getAddress();
        }
        return cur.getOffset();
    }

    /**
     * Every entry candidate behind {@code va}, nearest first, each one the byte after a 0xCC fill run in
     * the same section -- the same test SeedGaps' gap pass uses, with the same exclusions: a candidate
     * inside a known function, or one with another function between it and the evidence, is not tried.
     */
    private List<Long> fillStarts(long va, int maxBack, int limit) {
        List<Long> out = new ArrayList<>();
        Memory mem = currentProgram.getMemory();
        MemoryBlock block = mem.getBlock(toAddr(va));
        if (block == null || !block.isExecute()) return out;
        long lo = Math.max(block.getStart().getOffset(), va - maxBack);
        for (long i = va - 1; i > lo && out.size() < limit; i--) {
            if (!isFill(mem, i)) continue;
            Address cand = toAddr(i + 1);
            if (mem.getBlock(cand) != block) continue;
            if (getFunctionContaining(cand) != null) continue;
            if (entryBetween(cand, toAddr(va))) continue;
            out.add(cand.getOffset());
        }
        return out;
    }

    private boolean isFill(Memory mem, long va) {
        try {
            return mem.getByte(toAddr(va)) == (byte) 0xcc;
        } catch (MemoryAccessException e) {
            return false;
        }
    }

    private boolean entryBetween(Address cand, Address target) {
        Iterator<Function> it = currentProgram.getFunctionManager().getFunctions(cand, true);
        while (it.hasNext()) {
            Address entry = it.next().getEntryPoint();
            if (entry.compareTo(cand) > 0 && entry.compareTo(target) <= 0) return true;
        }
        return false;
    }
}
