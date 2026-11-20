// Decompile addresses that may not be functions yet: disassemble, create, then export.
// Same output shape as ExportAt.java, but the listing is not truncated.
// args: <addresses|@file> <out-dir>
// @category SG1
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import java.io.*;
import java.util.*;

public class ExportNew extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: ExportNew.java <addr[,addr...]|@file> <out-dir>");
            return;
        }
        List<String> addresses = new ArrayList<>();
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

        File outDir = new File(args[1]);
        outDir.mkdirs();
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        for (String a : addresses) {
            Address at = toAddr(Long.decode(a));
            Function fn = getFunctionAt(at);
            if (fn == null) {
                if (getInstructionAt(at) == null) {
                    println(a + ": disassembling");
                    disassemble(at);
                }
                try {
                    fn = createFunction(at, null);
                } catch (Exception e) {
                    println(a + ": createFunction failed: " + e);
                }
            }
            if (fn == null) {
                println(a + ": still no function");
                continue;
            }
            StringBuilder body = new StringBuilder();
            body.append("// ").append(fn.getName()).append(" @ ")
                .append(String.format("0x%08x", fn.getEntryPoint().getOffset()))
                .append("  size=").append(fn.getBody().getNumAddresses())
                .append("\n// game-derived output, never commit.\n\n");
            body.append("// ---- listing ----\n");
            int shown = 0;
            for (Instruction ins = getInstructionAt(fn.getEntryPoint());
                 ins != null && fn.getBody().contains(ins.getAddress()) && shown < 4000;
                 ins = ins.getNext(), shown++) {
                body.append("// ").append(ins.getAddress()).append("  ")
                    .append(ins.toString().replace("\n", " ")).append("\n");
            }
            body.append("\n// ---- decompiled ----\n");
            DecompileResults res = decomp.decompileFunction(fn, 120, monitor);
            if (res != null && res.decompileCompleted()) {
                body.append(res.getDecompiledFunction().getC());
            } else {
                body.append("// decompile failed\n");
            }
            String file = outDir.getPath() + "/" + String.format("%08x", fn.getEntryPoint().getOffset())
                    + "_" + fn.getName().replaceAll("[^A-Za-z0-9_]", "_") + ".c";
            try (PrintWriter w = new PrintWriter(new FileWriter(file))) { w.print(body); }
            println("wrote " + file);
        }
    }
}
