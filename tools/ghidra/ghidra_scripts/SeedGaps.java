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
//    body a jump cuts short, and the real entry is the run before it.
//
// 2. Data tables. A function reached only through a pointer -- a vtable slot, a class table -- has no
//    call site at all. UObject::ProcessEvent is one: 492 pointers to it in .rdata and not one direct
//    call. So every 4-byte value in a non-executable block that lands in executable memory with no
//    function is counted, and an address referenced by at least -pointerThreshold- separate values is
//    seeded. A single pointer can be a switch jump table target; a table that names the same address
//    hundreds of times is naming an entry point.
//
// No name is invented here: names still come from XbePost.
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.mem.MemoryBlock;
import java.io.*;
import java.util.*;

public class SeedGaps extends GhidraScript {
    private static final int MAX_ROUNDS = 4;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("usage: SeedGaps.java <asserts.tsv> [maxBackBytes]");
            return;
        }
        int maxBack = args.length > 1 ? Integer.decode(args[1]) : 0x1000;
        int pointerThreshold = args.length > 2 ? Integer.decode(args[2]) : 8;

        List<Long> evidence = new ArrayList<>();
        BufferedReader reader = new BufferedReader(new FileReader(args[0]));
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("#") || line.trim().isEmpty()) continue;
            String[] p = line.split("\t");
            // The xref column is where the 32-bit string VA sits, i.e. an operand inside the
            // instruction, not the instruction's own address.
            if (p.length >= 4) evidence.add(Long.decode(p[3]));
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

    private int countMissing(List<Long> evidence) {
        int missing = 0;
        for (long va : evidence) if (getFunctionContaining(toAddr(va)) == null) missing++;
        return missing;
    }

    /**
     * Plausible entries before {@code va}, nearest first, at most {@code limit} of them. The caller
     * tries them one round at a time: a candidate that produces a body not containing the site is
     * dropped, and the next one is the previous fill run.
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

    /** A second function between the candidate and the evidence site rules the candidate out. */
    private boolean entryBetween(Address cand, Address target) {
        Iterator<Function> it = currentProgram.getFunctionManager().getFunctions(cand, true);
        while (it.hasNext()) {
            Address entry = it.next().getEntryPoint();
            if (entry.compareTo(cand) > 0 && entry.compareTo(target) <= 0) return true;
        }
        return false;
    }

    private boolean isFill(Memory mem, long va) {
        try {
            return mem.getByte(toAddr(va)) == (byte) 0xcc;
        } catch (MemoryAccessException e) {
            return false;                     // unreadable: not a fill byte we can trust
        }
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
}
