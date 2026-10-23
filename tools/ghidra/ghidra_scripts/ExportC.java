// Decompile the functions belonging to one module (selected by name prefix) into C files.
//
// `symbols/xbox_functions.csv` names functions from the assert file they reference, so a module's
// functions can be selected by name prefix. The C is game-derived and belongs under the git-ignored
// work/ tree; the per-function inventory it also writes is what may be committed.
//
// args: <symbols.csv> <name-prefix> <out-dir> <out-index.csv>
// @category SG1
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.symbol.Reference;
import java.io.*;
import java.util.*;

public class ExportC extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 4) {
            println("usage: ExportC.java <symbols.csv> <name-prefix> <out-dir> <out-index.csv>");
            return;
        }
        String[] prefixes = args[1].split(",");
        File outDir = new File(args[2]);
        outDir.mkdirs();

        List<String[]> selected = new ArrayList<>();
        BufferedReader sym = new BufferedReader(new FileReader(args[0]));
        String line;
        int renamed = 0;
        while ((line = sym.readLine()) != null) {
            if (line.startsWith("address") || line.trim().isEmpty()) continue;
            String[] p = line.split(",", -1);
            if (p.length < 2) continue;
            // Apply the committed symbol names first: without them the decompiled C shows FUN_*
            // callers/callees and the module's own functions are indistinguishable from engine code.
            Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                    .getAddress(Long.decode(p[0]));
            Function fn = at == null ? null : getFunctionAt(at);
            if (fn != null && (fn.getName().startsWith("FUN_") || fn.getName().startsWith("sub_"))) {
                try {
                    fn.setName(p[1], ghidra.program.model.symbol.SourceType.USER_DEFINED);
                    renamed++;
                } catch (Exception ignored) {
                    // duplicate name: keep the first, the export still covers the address
                }
            }
            boolean match = false;
            for (String prefix : prefixes) if (p[1].startsWith(prefix)) match = true;
            if (match) selected.add(p);
        }
        sym.close();
        selected.sort(Comparator.comparing(p -> p[0]));
        println("applied " + renamed + " symbol names before decompiling");

        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        PrintWriter index = new PrintWriter(new FileWriter(args[3]));
        index.println("address,name,size,callees,strings,status");
        int ok = 0, failed = 0;
        TreeMap<String, Integer> unnamedCallees = new TreeMap<>();
        for (String[] row : selected) {
            Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                    .getAddress(Long.decode(row[0]));
            Function fn = getFunctionAt(at);
            if (fn == null) {
                index.println(row[0] + "," + row[1] + ",0,0,0,no-function");
                failed++;
                continue;
            }
            TreeSet<String> callees = new TreeSet<>();
            for (Function callee : fn.getCalledFunctions(monitor)) callees.add(callee.getName());
            for (String callee : callees)
                if (callee.startsWith("FUN_")) unnamedCallees.merge(callee, 1, Integer::sum);
            // Strings referenced from inside the function are the cheapest evidence of what it does
            // when its name only tells you the source file.
            TreeSet<String> strings = new TreeSet<>();
            for (Instruction ins = getInstructionAt(fn.getBody().getMinAddress());
                 ins != null && fn.getBody().contains(ins.getAddress());
                 ins = ins.getNext()) {
                for (Reference ref : ins.getReferencesFrom()) {
                    if (!ref.getReferenceType().isData()) continue;
                    String s = stringAt(ref.getToAddress());
                    if (s != null && !s.isEmpty()) strings.add(s);
                }
            }
            StringBuilder body = new StringBuilder();
            DecompileResults res = decomp.decompileFunction(fn, 60, monitor);
            String status;
            if (res != null && res.decompileCompleted()) {
                body.append("// ").append(fn.getName()).append(" @ ").append(row[0])
                    .append("  size=").append(fn.getBody().getNumAddresses()).append("\n")
                    .append("// PLAN 4.6 decompilation: game-derived output, never commit.\n\n")
                    .append(res.getDecompiledFunction().getC());
                status = "ok";
                ok++;
            } else {
                body.append("// ").append(fn.getName()).append(" @ ").append(row[0])
                    .append("\n// decompile failed\n");
                status = "decompile-failed";
                failed++;
            }
            String file = outDir.getPath() + "/" + row[0].replace("0x", "") + "_"
                    + row[1].replaceAll("[^A-Za-z0-9_]", "_") + ".c";
            try (PrintWriter w = new PrintWriter(new FileWriter(file))) { w.print(body); }
            index.println(row[0] + "," + row[1] + "," + fn.getBody().getNumAddresses() + ","
                    + callees.size() + "," + strings.size() + "," + status);
        }
        index.close();
        List<Map.Entry<String, Integer>> topHelpers = new ArrayList<>(unnamedCallees.entrySet());
        topHelpers.sort((a, b) -> b.getValue() - a.getValue());
        println("most-called unnamed helpers (name these next): "
                + topHelpers.subList(0, Math.min(10, topHelpers.size())));
        println("exported " + ok + " functions for '" + args[1] + "', " + failed + " failed -> "
                + outDir.getPath());
    }

    private String stringAt(Address at) {
        if (at == null) return null;
        DataType dt = getDataAt(at) == null ? null : getDataAt(at).getDataType();
        if (dt == null) return null;
        try {
            StringDataInstance s = StringDataInstance.getStringDataInstance(getDataAt(at));
            return s == null ? null : s.getStringValue();
        } catch (Exception e) {
            return null;
        }
    }
}
