// Seed functions for code the analyser cannot reach from a call site.
//
// A large part of this binary's VM is only ever entered through a function pointer: UFunction::Func,
// the GNatives slots, vtable entries. Nothing calls those directly, so the analyser never creates a
// function for them and the code around them stays unanalysed -- the bytecode interpreter is one of
// those gaps (docs/NATIVE_VM.md).
//
// The build's assert strings are the static evidence that code exists there: an MSVC assert site
// embeds __FILE__, and the site lies inside the function that asserts. This script takes the assert
// xref list, and for every site that falls in no function it walks backwards to the nearest
// candidate entry -- a byte after at least two 0xCC (int3) fill bytes, whose first instruction looks
// like a prologue -- and creates a function there. A candidate is kept only if the evidence site
// then lands inside the created function's body; anything else is counted as a miss and left alone.
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
            Map<Long, Long> picks = new TreeMap<>();      // candidate entry -> evidence site
            for (long va : evidence) {
                if (getFunctionContaining(toAddr(va)) != null) continue;
                Long start = findStart(va, maxBack);
                if (start != null && !seeds.contains(start)) picks.put(start, va);
            }
            if (picks.isEmpty()) break;
            rounds++;
            for (Map.Entry<Long, Long> e : picks.entrySet()) {
                Address at = toAddr(e.getKey());
                if (getFunctionAt(at) != null) continue;
                disassemble(at);
                Function fn = createFunction(at, null);
                if (fn == null) continue;
                created++;
                seeds.add(e.getKey());
                if (fn.getBody().contains(toAddr(e.getValue()))) {
                    validated++;
                } else if (tryRemove(fn)) {
                    // The candidate disassembled, but the function it made does not cover the assert
                    // that is the only reason to believe there is code here. Keeping it would invent
                    // a function; drop it and count the site as unresolved.
                    rejected++;
                    println("  rejected candidate " + at + ": evidence site " + toAddr(e.getValue())
                            + " is outside the body it produced");
                } else {
                    println("  kept (could not remove) " + at + " for evidence site " + toAddr(e.getValue()));
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

    private int countMissing(List<Long> evidence) {
        int missing = 0;
        for (long va : evidence) if (getFunctionContaining(toAddr(va)) == null) missing++;
        return missing;
    }

    /** Nearest plausible entry before {@code va}, or null when nothing qualifies. */
    private Long findStart(long va, int maxBack) {
        Memory mem = currentProgram.getMemory();
        MemoryBlock block = mem.getBlock(toAddr(va));
        if (block == null || !block.isExecute()) return null;
        long lo = Math.max(block.getStart().getOffset(), va - maxBack);
        for (long i = va - 1; i > lo; i--) {
            if (!isFill(mem, i)) continue;                          // the 0xCC before an entry
            Address cand = toAddr(i + 1);
            if (i - 1 > lo && !isFill(mem, i - 1)) continue;
            if (!looksLikePrologue(mem, cand)) continue;
            if (getFunctionContaining(cand) != null) continue;
            if (entryBetween(cand, toAddr(va))) continue;
            return cand.getOffset();
        }
        return null;
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
