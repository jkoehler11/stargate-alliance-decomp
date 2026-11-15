// Dump a body Ghidra's analysis left no function at: disassemble, create the function, then print
// the whole listing with every literal operand resolved and the decompiled body.
//
// ExportAt/ExportLiterals both start from `getFunctionAt`, and the symbol map's own rows include
// bodies (0x0024af30 among them) that analysis never made a function at -- the export then writes
// nothing for the address and the silence reads like a missing body. The address is a symbol-map
// function start, so the read is to make the function and then export it like any other.
//
// args: <addresses|@file> <out-file>
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.scalar.Scalar;
import java.io.*;

public class DumpBody extends GhidraScript {

    private String readString(Address addr) {
        if (addr == null) return null;
        try {
            StringBuilder wide = new StringBuilder();
            for (int i = 0; i < 512; i++) {
                Address a = addr.add(i * 2L);
                int lo = getByte(a) & 0xff;
                int hi = getByte(a.add(1)) & 0xff;
                if (lo == 0 && hi == 0) break;
                int ch = lo | (hi << 8);
                if (ch < 0x20 || ch == 0x7f) { wide.setLength(0); break; }
                wide.append((char) ch);
            }
            if (wide.length() >= 2) return "W\"" + wide + "\"";

            StringBuilder narrow = new StringBuilder();
            for (int i = 0; i < 512; i++) {
                int b = getByte(addr.add(i)) & 0xff;
                if (b == 0) break;
                if (b < 0x20 || b > 0x7e) { narrow.setLength(0); break; }
                narrow.append((char) b);
            }
            if (narrow.length() >= 2) return "A\"" + narrow + "\"";
        } catch (Exception ignored) {
            // unmapped or truncated: not a literal
        }
        return null;
    }

    private String literalAt(Object op) {
        if (op instanceof Scalar) {
            long v = ((Scalar) op).getUnsignedValue();
            if (v < 0x10000 || v > 0xffffffffL) return null;
            Address a = toAddr(v);
            if (a == null || getMemoryBlock(a) == null) return null;
            return readString(a);
        }
        if (op instanceof Address) {
            Address a = (Address) op;
            if (getMemoryBlock(a) == null) return null;
            return readString(a);
        }
        return null;
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: DumpBody.java <addr[,addr...]|@file> <out-file>");
            return;
        }
        java.util.List<String> addresses = new java.util.ArrayList<>();
        if (args[0].startsWith("@")) {
            BufferedReader r = new BufferedReader(new FileReader(args[0].substring(1)));
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty() && !line.startsWith("#")) addresses.add(line);
            }
            r.close();
        } else {
            for (String a : args[0].split(",")) if (!a.trim().isEmpty()) addresses.add(a.trim());
        }

        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);

        try (PrintWriter w = new PrintWriter(new FileWriter(args[1]))) {
            for (String a : addresses) {
                Address at = toAddr(Long.decode(a));
                Function fn = getFunctionAt(at);
                if (fn == null) {
                    Function containing = getFunctionContaining(at);
                    if (containing == null) {
                        println(a + ": no function, disassembling and creating one");
                        disassemble(at);
                        try {
                            fn = createFunction(at, null);
                        } catch (Exception e) {
                            println(a + ": createFunction failed: " + e);
                        }
                    } else {
                        fn = containing;
                    }
                }
                if (fn == null) {
                    w.println("## " + a + "  NO FUNCTION");
                    continue;
                }
                w.println("## " + fn.getName() + " @ "
                        + String.format("0x%08x", fn.getEntryPoint().getOffset())
                        + "  size=" + fn.getBody().getNumAddresses());
                Listing listing = currentProgram.getListing();
                int count = 0;
                for (Instruction ins = listing.getInstructionAt(fn.getEntryPoint());
                     ins != null && fn.getBody().contains(ins.getAddress());
                     ins = ins.getNext()) {
                    StringBuilder line = new StringBuilder();
                    line.append(String.format("%08x  ", ins.getAddress().getOffset()))
                        .append(ins.toString().replace("\n", " "));
                    StringBuilder lits = new StringBuilder();
                    for (int i = 0; i < ins.getNumOperands(); i++) {
                        for (Object op : ins.getOpObjects(i)) {
                            String s = literalAt(op);
                            if (s != null && lits.indexOf(s) < 0) {
                                if (lits.length() > 0) lits.append(" | ");
                                lits.append(s);
                            }
                        }
                    }
                    if (lits.length() > 0) line.append("   ; ").append(lits);
                    w.println(line);
                    count++;
                }
                println(fn.getName() + ": " + count + " instructions");
                DecompileResults res = ifc.decompileFunction(fn, 60, monitor);
                w.println();
                w.println("// ---- decompiled ----");
                w.println();
                if (res != null && res.decompileCompleted()) {
                    w.println(res.getDecompiledFunction().getC());
                } else {
                    w.println("// decompile failed");
                }
                w.println();
            }
        }
        println("wrote " + args[1]);
    }
}
