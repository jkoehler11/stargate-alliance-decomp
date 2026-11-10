// Verify that the addresses the native registration table names are function starts.
//
// A native's body is reached only through the registration table, so the call-graph analyzer never
// makes a function out of it and `symbols/xbox_functions.csv` has no row. This script asks the
// disassembler rather than a byte pattern: for each address it creates a function and reports what
// came back — accepted with a body, already a function, or already inside a body somebody else
// produced (which is the one outcome that would make the address not a start). Keeping the body it
// produced is also what makes the second address of a shared tail visible: creating a function at
// an address that is already inside the body of one created earlier in this same pass reports
// `inside`, so a native body cannot silently subsume the next one.
//
// The name comes from the caller's file (`0x0040510 intUObjectexecSetUTracing`), which is the
// registration table's own spelling; nothing is invented here.
//
// args: <@file|file> <out.csv>; each line `<va> [name]`
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import java.io.*;
import java.util.*;

public class NativeCensus extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: NativeCensus.java <addrs|@addrs> <out.csv>");
            return;
        }
        List<String[]> rows = new ArrayList<>();
        BufferedReader r = new BufferedReader(new FileReader(args[0].startsWith("@") ? args[0].substring(1) : args[0]));
        String line;
        while ((line = r.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] p = line.split("\\s+", 2);
            rows.add(new String[]{p[0], p.length > 1 ? p[1].trim() : ""});
        }
        r.close();
        rows.sort(Comparator.comparingLong(a -> Long.decode(a[0])));

        StringBuilder out = new StringBuilder(
                "body_va,name,verdict,container,body_bytes,instructions,last_mnemonic,ends_terminal,first_bytes\n");
        int accepted = 0, existing = 0, inside = 0, rejected = 0;
        for (String[] row : rows) {
            long va = Long.decode(row[0]);
            Address at = toAddr(va);
            String verdict = "", container = "";
            long bytes = -1;
            int insn = -1;
            String last = "", terminal = "";
            Function prior = getFunctionContaining(at);
            if (prior != null) {
                container = prior.getName() + "@0x" + Long.toHexString(prior.getEntryPoint().getOffset());
                if (prior.getEntryPoint().equals(at)) {
                    verdict = "existing";
                    existing++;
                } else {
                    verdict = "inside";
                    inside++;
                }
                bytes = prior.getBody().getNumAddresses();
            } else {
                disassemble(at);
                Function fn = null;
                try {
                    fn = createFunction(at, row[1].isEmpty() ? null : row[1]);
                } catch (Exception e) {
                    container = "exception: " + e.getMessage();
                }
                if (fn == null) {
                    verdict = "rejected";
                    rejected++;
                } else {
                    verdict = "accepted";
                    accepted++;
                    bytes = fn.getBody().getNumAddresses();
                    int n = 0;
                    for (Instruction i : currentProgram.getListing().getInstructions(fn.getBody(), true)) {
                        if (n++ > 200000) break;
                    }
                    insn = n;
                    // The body's max address is its last *byte*, and instructions are indexed by their
                    // start, so `getInstructionAt` on it answers null for every function.
                    Instruction it = getInstructionContaining(fn.getBody().getMaxAddress());
                    if (it != null) {
                        last = it.getMnemonicString();
                        terminal = String.valueOf(it.getFlowType().isTerminal());
                    }
                }
            }
            out.append(String.format("0x%08x,%s,%s,%s,%d,%d,%s,%s,%s%n",
                    va, row[1], verdict, container, bytes, insn, last, terminal, firstBytes(at, 8)));
        }
        try (PrintWriter w = new PrintWriter(new FileWriter(args[1]))) {
            w.print(out);
        }
        println("native census: " + accepted + " accepted, " + existing + " already a function, "
                + inside + " inside another body, " + rejected + " rejected, of " + rows.size());
        println("functions now: " + currentProgram.getFunctionManager().getFunctionCount());
    }

    private String firstBytes(Address at, int n) {
        StringBuilder s = new StringBuilder();
        for (int i = 0; i < n; i++) {
            try {
                s.append(String.format("%02x", currentProgram.getMemory().getByte(at.add(i)) & 0xff));
            } catch (Exception e) {
                break;
            }
        }
        return s.toString();
    }
}
