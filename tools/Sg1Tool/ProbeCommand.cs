using UELib;
using UELib.Core;

namespace Sg1Tool;

/// <summary>Debugging aid: dump the export table of one package straight from the reader.</summary>
internal static class ProbeCommand
{
    public static int Run(string dumpRoot, string packageName, string init)
    {
        var path = Dump.Packages(dumpRoot)
            .First(p => Path.GetFileNameWithoutExtension(p)
                .Equals(packageName, StringComparison.OrdinalIgnoreCase));
        var pkg = UnrealLoader.LoadPackage(path, FileAccess.Read);
        var flags = init.Equals("all", StringComparison.OrdinalIgnoreCase)
            ? UnrealPackage.InitFlags.All
            : UnrealPackage.InitFlags.Construct | UnrealPackage.InitFlags.RegisterClasses;
        pkg.InitializePackage(flags);

        var exports = pkg.Objects.Where(Dump.IsExport).ToList();
        Console.WriteLine($"init={flags} exports={exports.Count} exportsTable={pkg.Summary.ExportCount}");
        foreach (var group in exports.GroupBy(o => o.Class?.Name.ToString() ?? "?").OrderByDescending(g => g.Count()))
            Console.WriteLine($"  {group.Key,-24} {group.Count()}");
        Console.WriteLine("first 12 exports:");
        foreach (var obj in exports.Take(12))
            Console.WriteLine($"  [{(int)obj,4}] {obj.Name,-24} class={obj.Class?.Name} outer={obj.Outer?.Name} path={obj.GetReferencePath()}");
        return 0;
    }
}
