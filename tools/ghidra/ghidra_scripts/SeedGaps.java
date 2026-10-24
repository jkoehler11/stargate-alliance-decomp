// Seed functions for code the analyser cannot reach from a call site.
//
// A large part of this binary's VM is only ever entered through a function pointer: UFunction::Func,
// the GNatives slots, vtable entries. Nothing calls those directly, so the analyser never creates a
// function for them and the code around them stays unanalysed -- the bytecode interpreter is one of
// those gaps (docs/NATIVE_VM.md).
//
// Two kinds of static evidence are used:
//
// 1. Assert strings. An MSVC assert site embeds __FILE__, and the site lies inside the function that
//    asserts, so a site with no function means code the analyser missed. For each one this walks
//    backwards over the fill bytes and collects the plausible entries before the site (nearest
//    first, a few of them), then creates a function at one and keeps it only if the evidence site
//    lands inside the body it produced. Trying one candidate per round is what makes an otherwise
//    unreachable site resolvable: the nearest fill run is sometimes the tail of a function whose
//    body a jump cuts short, and the real entry is the run before it. A fill run is the only filter
//    applied before that test: an opening-byte list rejects real entries (they can open with an
//    argument load), so a candidate the list does not know has to show, instead, that the instruction
//    covering the site is a PUSH of the string the row names. Without that second test a dword in an
//    executable data section -- the xref scan cannot tell one from a call site -- becomes a function.
//    The first pass keeps its opening-byte list, so its results do not move; the entries recovered
//    this way are listed in the gap CSV, like block seeds, so nothing mistakes them for pass-1 entries.
//
// 2. Data tables. A function reached only through a pointer -- a vtable slot, a class table -- has no
//    call site at all. UObject::ProcessEvent is one: 492 pointers to it in .rdata and not one direct
//    call. So every 4-byte value in a non-executable block that lands in executable memory with no
//    function is counted, and an address referenced by at least -pointerThreshold- separate values is
//    seeded. A single pointer can be a switch jump table target; a table that names the same address
//    hundreds of times is naming an entry point.
//
// 3. Block heads, for the sites the first two passes reject. An assert can sit in a block that the
//    function entry reaches only through a jump, so linear flow from the nearest entry never covers
//    it. This pass disassembles the gap before the site, walks back to the head of the block the site
//    sits in, and creates a function there. These are NOT function entries and are recorded as such:
//    every one is written to the block-seed CSV so nothing else can mistake a promoted block for an
//    entry the build names.
//
// No name is invented here: names still come from XbePost.
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.FlowType;
import ghidra.program.model.scalar.Scalar;
import java.io.*;
import java.util.*;

public class SeedGaps extends GhidraScript {
    private static final int MAX_ROUNDS = 4;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: SeedGaps.java <asserts.tsv> [maxBackBytes] [pointerThreshold] [blockCsv] [gapCsv]");
            return;
        }
        int maxBack = args.length > 1 ? Integer.decode(args[1]) : 0x1000;
        int pointerThreshold = args.length > 2 ? Integer.decode(args[2]) : 8;
        String blockCsv = args.length > 3 ? args[3] : null;
        String gapCsv = args.length > 4 ? args[4] : null;

        List<Long> evidence = new ArrayList<>();
        Map<Long, Long> stringAt = new HashMap<>();      // evidence site -> the file string it pushes
        BufferedReader reader = new BufferedReader(new FileReader(args[0]));
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("#") || line.trim().isEmpty()) continue;
            String[] p = line.split("\t");
            // The xref column is where the 32-bit string VA sits, i.e. an operand inside the
            // instruction, not the instruction's own address.
            if (p.length >= 4) {
                long site = Long.decode(p[3]);
                evidence.add(site);
                stringAt.put(site, Long.decode(p[2]));
            }
        }
        reader.close();

        int before = countMissing(evidence);
        println("evidence sites: " + evidence.size() + ", in no function: " + before);

        int created = 0, validated = 0, rounds = 0, rejected = 0;
        Set<Long> seeds = new HashSet<>();
        for (int round = 0; round < MAX_ROUNDS; round++) {
            Map<Long, List<Long>> picks = new TreeMap<>();   // candidate entry -> evidence sites
            for (long va : evidence) {
                if (getFunctionContaining(toAddr(va)) != null) continue;
                // One attempt per site per round: the nearest candidate that no earlier round has
                // already tried and thrown away. Rejections are retried on the next candidate.
                for (Long start : findStarts(va, maxBack, 4)) {
                    if (seeds.contains(start)) continue;
                    picks.computeIfAbsent(start, k -> new ArrayList<>()).add(va);
                    break;
                }
            }
            if (picks.isEmpty()) break;
            rounds++;
            for (Map.Entry<Long, List<Long>> e : picks.entrySet()) {
                Address at = toAddr(e.getKey());
                if (getFunctionAt(at) != null) continue;
                // A candidate picked earlier in this round can now sit inside a body another
                // candidate just produced. Creating a function there would split the real one.
                if (getFunctionContaining(at) != null) continue;
                disassemble(at);
                Function fn = createFunction(at, null);
                if (fn == null) continue;
                created++;
                seeds.add(e.getKey());
                long site = e.getValue().get(0);
                if (fn.getBody().contains(toAddr(site))) {
                    validated++;
                } else if (tryRemove(fn)) {
                    // The candidate disassembled, but the function it made does not cover the assert
                    // that is the only reason to believe there is code here. Keeping it would invent
                    // a function; drop it and count the site as unresolved.
                    rejected++;
                    println("  rejected candidate " + at + ": evidence site " + toAddr(site)
                            + " is outside the body it produced");
                } else {
                    println("  kept (could not remove) " + at + " for evidence site " + toAddr(site));
                }
            }
            // Let the analyser follow what the new functions reveal: callees, jump tables, and the
            // next batch of evidence sites that are now inside a function.
            analyzeChanges(currentProgram);
        }

        println("seeded functions: " + created + " in " + rounds + " round(s); "
                + validated + " verified by containing their evidence site, "
                + rejected + " rejected as wrong entries");
        println("evidence sites in no function: " + before + " -> " + countMissing(evidence));

        // Second source: entry points named by data tables rather than by code.
        int ptrSeeded = seedFromPointers(pointerThreshold);
        println("seeded " + ptrSeeded + " entries from data tables (>= " + pointerThreshold
                + " pointers to one address)");
        // Fourth source: an entry inside a gap, for a site the passes above could not reach. Runs
        // before the block pass so a real entry is preferred over a promoted block.
        int gapSeeded = seedFromGaps(evidence, stringAt, maxBack, gapCsv);
        println("seeded " + gapSeeded + " entries from gap decodes"
                + (gapCsv != null ? " (list: " + gapCsv + ")" : ""));
        // Third source: block heads for the sites the passes above could not cover.
        int blockSeeded = seedFromBlocks(evidence, maxBack, blockCsv);
        println("seeded " + blockSeeded + " block heads for sites with no reachable entry"
                + (blockCsv != null ? " (list: " + blockCsv + ")" : ""));
        println("evidence sites in no function, final: " + countMissing(evidence));
        println("functions now: " + currentProgram.getFunctionManager().getFunctionCount());
    }

    /** Drop a candidate that did not cover its evidence; returns true when it is gone. */
    private boolean tryRemove(Function fn) {
        Address entry = fn.getEntryPoint();
        try {
            removeFunction(fn);
        } catch (Exception e) {
            return false;
        }
        return getFunctionAt(entry) == null;
    }

    /**
     * Seed entry points that only data refers to. Every aligned dword in a non-executable block that
     * points into executable memory with no function is counted per target; targets with enough
     * referrers are created as functions and analysed.
     */
    private int seedFromPointers(int threshold) {
        Memory mem = currentProgram.getMemory();
        Map<Long, Integer> refs = new TreeMap<>();
        for (MemoryBlock b : mem.getBlocks()) {
            // The section flag that marks "executable" is set on this build's .rdata and .data too, so
            // the filter cannot be the flag. A block that holds functions is code; a block that holds
            // none is a table, and its dwords are pointers.
            if (!b.isInitialized() || b.getSize() > 0x8000000L) continue;
            if (currentProgram.getFunctionManager().getFunctions(b.getStart(), true).hasNext()) continue;
            long end = b.getEnd().getOffset();
            for (long a = b.getStart().getOffset(); a + 4 <= end; a += 4) {
                int v;
                try {
                    v = mem.getInt(toAddr(a));
                } catch (MemoryAccessException e) {
                    continue;
                }
                long target = v & 0xffffffffL;
                MemoryBlock tb = mem.getBlock(toAddr(target));
                if (tb == null || !tb.isExecute()) continue;
                if (getFunctionContaining(toAddr(target)) != null) continue;
                refs.merge(target, 1, Integer::sum);
            }
        }
        int created = 0;
        for (Map.Entry<Long, Integer> e : refs.entrySet()) {
            if (e.getValue() < threshold) continue;
            Address at = toAddr(e.getKey());
            if (getFunctionAt(at) != null) continue;
            disassemble(at);
            if (createFunction(at, null) != null) created++;
        }
        if (created > 0) analyzeChanges(currentProgram);
        return created;
    }

    /**
     * Seed an entry for a site the first three sources leave, by decoding the gap it sits in.
     *
     * The candidate is the byte after a 0xCC fill run (pass 1's guess does not apply here), and three
     * measurements decide whether it is an entry: the function created there contains the site; the
     * instruction covering the site is a PUSH whose immediate is the file string the asserts table
     * names for that row; and the body ends at a terminator rather than stopping in mid-flow. A dword
     * in an executable data section passes none of them, and a candidate that starts inside the body
     * of a function the analysis already has is never tried (`fillStarts` skips it).
     */
    private int seedFromGaps(List<Long> evidence, Map<Long, Long> stringAt, int maxBack, String csvPath)
            throws Exception {
        int created = 0;
        StringBuilder rows = new StringBuilder("function_va,evidence_va,evidence_bytes,evidence\n");
        for (long va : evidence) {
            if (getFunctionContaining(toAddr(va)) != null) continue;
            for (Long cand : fillStarts(va, maxBack, 8)) {
                Address at = toAddr(cand);
                if (getFunctionContaining(at) != null || getFunctionAt(at) != null) continue;
                disassemble(at);
                Function fn = createFunction(at, null);
                if (fn == null) continue;
                if (fn.getBody().contains(toAddr(va)) && pushesString(va, stringAt.get(va))
                        && endsInTerminator(fn)) {
                    created++;
                    rows.append(String.format("0x%08x,0x%08x,%d,entry found by decoding the gap%n",
                            cand, va, fn.getBody().getNumAddresses()));
                    break;
                }
                tryRemove(fn);
            }
        }
        if (csvPath != null) {
            try (PrintWriter w = new PrintWriter(new FileWriter(csvPath))) {
                w.print(rows);
            }
        }
        return created;
    }

    /** True when the last instruction of the body returns, jumps or stops: a body that just runs out
     *  of decode is a reading aid here, not a function. */
    private boolean endsInTerminator(Function fn) {
        Instruction ins = getInstructionAt(fn.getBody().getMaxAddress());
        if (ins == null) return false;
        String m = ins.getMnemonicString().toUpperCase();
        if (m.startsWith("RET") || m.equals("JMP") || m.equals("HLT")) return true;
        return ins.getFlowType().isTerminal();
    }

    private int countMissing(List<Long> evidence) {
        int missing = 0;
        for (long va : evidence) if (getFunctionContaining(toAddr(va)) == null) missing++;
        return missing;
    }

    /**
     * Promote the block that contains each leftover evidence site. The block head is the first
     * instruction after a gap, a terminator, a jump or a call, found by disassembling the gap before
     * the site and walking back along instruction boundaries. A promoted block is a reading aid, not
     * an entry point: the CSV this writes is the record of that, and the doc says so.
     */
    private int seedFromBlocks(List<Long> evidence, int maxBack, String csvPath) throws Exception {
        int created = 0;
        StringBuilder rows = new StringBuilder("function_va,evidence_va,evidence_bytes,evidence\n");
        for (long va : evidence) {
            if (getFunctionContaining(toAddr(va)) != null) continue;
            Long head = blockHead(va, maxBack);
            if (head == null) continue;
            Address at = toAddr(head);
            if (getFunctionContaining(at) != null || getFunctionAt(at) != null) continue;
            Function fn = createFunction(at, null);
            if (fn == null) continue;
            if (!fn.getBody().contains(toAddr(va))) {
                tryRemove(fn);
                continue;
            }
            created++;
            rows.append(String.format("0x%08x,0x%08x,%d,promoted block head (not a function entry)%n",
                    head, va, fn.getBody().getNumAddresses()));
        }
        if (csvPath != null) {
            try (PrintWriter w = new PrintWriter(new FileWriter(csvPath))) {
                w.print(rows);
            }
        }
        return created;
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
     * Plausible entries before {@code va}, nearest first, at most {@code limit} of them. The caller
     * tries them one round at a time: a candidate that produces a body not containing the site is
     * dropped, and the next one is the previous fill run.
     *
     * The only static requirement is the one the compiler guarantees: the candidate follows a 0xCC
     * fill byte, which is where this build puts a function boundary. An earlier version also demanded
     * one of the two-byte shapes MSVC opens a function with, and that guess is what hid 47 of the 49
     * residual assert sites -- they open with an argument load such as `mov eax,[esp+8]`, which the
     * list does not have. What decides is the measurement afterwards, not a byte pattern: the body the
     * candidate produces has to contain the evidence site.
     */
    private List<Long> findStarts(long va, int maxBack, int limit) {
        List<Long> out = new ArrayList<>();
        Memory mem = currentProgram.getMemory();
        MemoryBlock block = mem.getBlock(toAddr(va));
        if (block == null || !block.isExecute()) return out;
        long lo = Math.max(block.getStart().getOffset(), va - maxBack);
        for (long i = va - 1; i > lo && out.size() < limit; i--) {
            if (!isFill(mem, i)) continue;                          // the 0xCC before an entry
            Address cand = toAddr(i + 1);
            // Two fill bytes, or a single one right after a ret (this compiler pads short
            // functions with one 0xCC and the previous body ends in `ret`).
            boolean padded = i - 1 > lo && (isFill(mem, i - 1) || isRet(mem, i - 1));
            if (!padded && !isFill(mem, i - 1)) continue;
            if (!looksLikePrologue(mem, cand)) continue;
            if (getFunctionContaining(cand) != null) continue;
            if (entryBetween(cand, toAddr(va))) continue;
            out.add(cand.getOffset());
        }
        return out;
    }

    private boolean isRet(Memory mem, long va) {
        try {
            byte b = mem.getByte(toAddr(va));
            return b == (byte) 0xc3 || b == (byte) 0xc2;
        } catch (MemoryAccessException e) {
            return false;
        }
    }

    /**
     * The same scan without the opening-byte list: every entry candidate behind {@code va}, nearest
     * first, each one the byte after a 0xCC fill run in the same section. This is what the fourth pass
     * uses, because the list is a guess and it rejects real entries -- 47 of the 49 assert sites that
     * had no function open with an argument load (`mov eax,[esp+8]`) that the list does not contain.
     * Pass 4 pays for dropping the guess by demanding more evidence than pass 1: the body has to end
     * in a terminator and the site has to be a PUSH of the file string the row names.
     */
    private List<Long> fillStarts(long va, int maxBack, int limit) {
        List<Long> out = new ArrayList<>();
        Memory mem = currentProgram.getMemory();
        MemoryBlock block = mem.getBlock(toAddr(va));
        if (block == null || !block.isExecute()) return out;
        long lo = Math.max(block.getStart().getOffset(), va - maxBack);
        for (long i = va - 1; i > lo && out.size() < limit; i--) {
            if (!isFill(mem, i)) continue;                          // the 0xCC before an entry
            Address cand = toAddr(i + 1);
            if (mem.getBlock(cand) != block) continue;              // fill runs to the end of a section
            if (getFunctionContaining(cand) != null) continue;
            if (entryBetween(cand, toAddr(va))) continue;
            out.add(cand.getOffset());
        }
        return out;
    }

    /** A second function between the candidate and the evidence site rules the candidate out. */
    private boolean entryBetween(Address cand, Address target) {
        Iterator<Function> it = currentProgram.getFunctionManager().getFunctions(cand, true);
        while (it.hasNext()) {
            Address entry = it.next().getEntryPoint();
            if (entry.compareTo(cand) > 0 && entry.compareTo(target) <= 0) return true;
        }
        return false;
    }

    /**
     * The evidence was a 32-bit string address in an "executable" section, and this build marks its
     * data sections executable too, so an address alone can be a dword in a table. What makes a site
     * a call site is the instruction that reads it: the MSVC assert macro pushes the file string, so
     * the operand has to be the immediate of a PUSH, and its value has to be the string the row names.
     * A candidate that has to rely on this test is one the prologue list does not know, so the test
     * is what stands between it and a guessed entry.
     */
    private boolean pushesString(long site, Long stringVa) {
        if (stringVa == null) return false;
        Instruction ins = getInstructionContaining(toAddr(site));
        if (ins == null || ins.getNumOperands() < 1) return false;
        if (!ins.getMnemonicString().equalsIgnoreCase("PUSH")) return false;
        Scalar s = ins.getScalar(0);
        return s != null && (s.getUnsignedValue() & 0xffffffffL) == (stringVa & 0xffffffffL);
    }

    /** MSVC 7.1 opening instructions seen at this build's real function entries. */
    private boolean looksLikePrologue(Memory mem, Address at) {
        int b0, b1;
        try {
            b0 = mem.getByte(at) & 0xff;
            b1 = mem.getByte(at.add(1)) & 0xff;
        } catch (MemoryAccessException e) {
            return false;
        }
        if (b0 >= 0x50 && b0 <= 0x57) return true;      // push reg (ebp, ecx, ebx, esi, edi ...)
        if (b0 == 0x6a || b0 == 0x68) return true;      // push imm8 / imm32 (incl. the SEH frame)
        if (b0 == 0x83 || b0 == 0x81) return true;      // sub esp, imm
        if (b0 == 0x64 && b1 == 0xa1) return true;      // mov eax, fs:[0] (SEH prologue)
        if (b0 == 0x8b && b1 == 0xff) return true;      // mov edi, edi (hotpatch)
        return b0 == 0xb8;                              // mov eax, imm32
    }


    private boolean isFill(Memory mem, long va) {
        try {
            return mem.getByte(toAddr(va)) == (byte) 0xcc;
        } catch (MemoryAccessException e) {
            return false;                     // unreadable: not a fill byte we can trust
        }
    }

}
