// Host-side visibility sampler for tools/build_o3ds_pvs.py.
//
// For every sample camera position, renders the six faces of a slightly
// widened cube map in software: first the depth of every opaque occluder
// (depth-writing, not alpha-tested, not blended), then, conservatively, every
// triangle against that depth eroded by one pixel. A block (128 consecutive
// triangles of a submesh, as Model::enableStaticCulling builds them) is
// potentially visible from the sample when any of its triangles has a covered
// pixel at or in front of the eroded occluder depth.
//
// Input (little endian):  "PVSI" u32 version=1|2, u32 triangles, u32 blocks,
//   u32 samples, u32 resolution, f32 near, f32 fovDegrees, i32 frontSign,
//   triangles * {f32 xyz[9]; u32 block; u32 flags (1 occluder, 2 single sided)},
//   samples * f32 xyz[3],
//   version 2 only: u32 boxes, boxes * f32 {min xyz, max xyz}.
// Output: samples * (ceil(blocks/8) + ceil(boxes/8)) bytes; bit b of a row's
// first part = block b visible, of its second part = box b visible.
//
// Boxes (version 2, tools/build_o3ds_object_pvs.py): a box is potentially
// visible from the sample when its clipped projection's bounding rectangle
// (padded by a pixel) has a pixel whose eroded occluder depth is at or behind
// the box's nearest point. If any interior point of a box is visible, so is
// the point where the eye's ray enters the box, so this covers anything the
// box contains. A box holding the eye (within the near plane) is visible.
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>

namespace {
struct Tri {float v[9];uint32_t block,flags;};
struct Box {float lo[3],hi[3];};
struct V3 {double x,y,z;};
V3 sub(V3 a,V3 b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
double dot(V3 a,V3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
V3 cross(V3 a,V3 b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}

struct Params {uint32_t res;double near,k;int frontSign;};
struct Face {V3 r,u,f;};
const Face Faces[6]={
    {{0,0,-1},{0,1,0},{1,0,0}},{{0,0,1},{0,1,0},{-1,0,0}},
    {{1,0,0},{0,0,-1},{0,1,0}},{{1,0,0},{0,0,1},{0,-1,0}},
    {{1,0,0},{0,1,0},{0,0,1}},{{-1,0,0},{0,1,0},{0,0,-1}},
};

struct Worker {
    const Params& p;const std::vector<Tri>& tris;const std::vector<uint32_t>& occluders;const std::vector<Box>& boxes;
    std::vector<float> depth,eroded;std::vector<uint8_t> seen,boxSeen;
    // Minimum (farthest) eroded depth per 2^l x 2^l tile, level 0 = eroded.
    std::vector<std::vector<float>> pyramid;
    Worker(const Params& p,const std::vector<Tri>& t,const std::vector<uint32_t>& o,uint32_t blocks,const std::vector<Box>& b)
        :p(p),tris(t),occluders(o),boxes(b),depth(size_t(p.res)*p.res),eroded(size_t(p.res)*p.res),seen(blocks),boxSeen(b.size()){}

    // Camera-space polygon of a triangle clipped to z >= near; returns vertex count.
    int clip(const Tri& t,V3 eye,const Face& f,V3* out)const{
        V3 c[3];
        for(int i=0;i<3;++i){V3 d=sub({t.v[i*3],t.v[i*3+1],t.v[i*3+2]},eye);c[i]={dot(d,f.r),dot(d,f.u),dot(d,f.f)};}
        // Trivial rejects against the widened face.
        const double k=p.k;
        auto all=[&](auto pred){return pred(c[0])&&pred(c[1])&&pred(c[2]);};
        if(all([&](V3 q){return q.z<p.near;}))return 0;
        if(all([&](V3 q){return q.x*k>q.z;})||all([&](V3 q){return -q.x*k>q.z;})||
           all([&](V3 q){return q.y*k>q.z;})||all([&](V3 q){return -q.y*k>q.z;}))return 0;
        int n=0;
        for(int i=0;i<3;++i){
            V3 a=c[i],b=c[(i+1)%3];bool ia=a.z>=p.near,ib=b.z>=p.near;
            if(ia)out[n++]=a;
            if(ia!=ib){double s=(p.near-a.z)/(b.z-a.z);out[n++]={a.x+(b.x-a.x)*s,a.y+(b.y-a.y)*s,p.near};}
        }
        return n;
    }
    // Rasterises one clipped triangle (camera space). Pass 0 writes depth
    // (centre sampled); pass 1 returns true at the first conservatively
    // covered pixel at or in front of the eroded depth.
    bool raster(V3 a,V3 b,V3 c,int pass){
        const double half=.5*p.res,k=p.k;
        auto proj=[&](V3 q,double& x,double& y,double& w){w=1./q.z;x=(q.x*k*w+1.)*half;y=(q.y*k*w+1.)*half;};
        double x0,y0,w0,x1,y1,w1,x2,y2,w2;proj(a,x0,y0,w0);proj(b,x1,y1,w1);proj(c,x2,y2,w2);
        double area=(x1-x0)*(y2-y0)-(x2-x0)*(y1-y0);
        if(pass==0&&std::fabs(area)<1e-12)return false;
        if(area<0){std::swap(x1,x2);std::swap(y1,y2);std::swap(w1,w2);area=-area;}
        const double pad=pass?1.:0.;
        int minX=std::max(0,int(std::floor(std::min({x0,x1,x2})-pad))),maxX=std::min(int(p.res)-1,int(std::ceil(std::max({x0,x1,x2})+pad)));
        int minY=std::max(0,int(std::floor(std::min({y0,y1,y2})-pad))),maxY=std::min(int(p.res)-1,int(std::ceil(std::max({y0,y1,y2})+pad)));
        if(minX>maxX||minY>maxY)return false;
        // Edge functions E(x,y)=A x+B y+C, >= 0 inside (counter-clockwise after the swap).
        double A[3]={y0-y1,y1-y2,y2-y0},B[3]={x1-x0,x2-x1,x0-x2};
        double C[3]={x0*y1-x1*y0,x1*y2-x2*y1,x2*y0-x0*y2};
        double slack[3]={0,0,0};
        if(pass)for(int i=0;i<3;++i)slack[i]=.5*(std::fabs(A[i])+std::fabs(B[i]))+1e-9;
        // 1/z is affine in screen space.
        double dA=0,dB=0,dC=0;
        if(area>1e-12){
            dA=((w1-w0)*(y2-y0)-(w2-w0)*(y1-y0))/area;
            dB=((w2-w0)*(x1-x0)-(w1-w0)*(x2-x0))/area;
            dC=w0-dA*x0-dB*y0;
        }else{dC=std::max({w0,w1,w2});}
        const double wMax=std::max({w0,w1,w2}),wMin=std::min({w0,w1,w2}),reach=.5*(std::fabs(dA)+std::fabs(dB));
        for(int y=minY;y<=maxY;++y){
            const double py=y+.5;float* row=(pass?eroded.data():depth.data())+size_t(y)*p.res;
            for(int x=minX;x<=maxX;++x){
                const double px=x+.5;
                bool inside=true;
                for(int i=0;i<3&&inside;++i)inside=A[i]*px+B[i]*py+C[i]>=-slack[i];
                if(!inside)continue;
                double w=dA*px+dB*py+dC;
                if(pass==0){w=std::clamp(w,wMin,wMax);if(w>row[x])row[x]=float(w);}
                else{w=std::min(w+reach,wMax);if(w>=double(row[x])*(1.-1e-3))return true;}
            }
        }
        return false;
    }
    bool frontFacing(const Tri& t,V3 eye)const{
        if(!(t.flags&2))return true;
        V3 a{t.v[0],t.v[1],t.v[2]},b{t.v[3],t.v[4],t.v[5]},c{t.v[6],t.v[7],t.v[8]};
        return dot(sub(eye,a),cross(sub(b,a),sub(c,a)))*p.frontSign>0;
    }
    void buildPyramid(){
        pyramid.resize(1);pyramid[0]=eroded;
        for(uint32_t size=p.res;size>1;size=(size+1)/2){
            const auto& fine=pyramid.back();const uint32_t half=(size+1)/2;std::vector<float> coarse(size_t(half)*half);
            for(uint32_t y=0;y<half;++y)for(uint32_t x=0;x<half;++x){
                float m=fine[size_t(2*y)*size+2*x];
                if(2*x+1<size)m=std::min(m,fine[size_t(2*y)*size+2*x+1]);
                if(2*y+1<size){m=std::min(m,fine[size_t(2*y+1)*size+2*x]);if(2*x+1<size)m=std::min(m,fine[size_t(2*y+1)*size+2*x+1]);}
                coarse[size_t(y)*half+x]=m;
            }
            pyramid.push_back(std::move(coarse));
        }
    }
    // True if some pixel in [x0,x1]x[y0,y1] has eroded depth <= limit.
    bool anyAtOrBehind(int level,int tx,int ty,int x0,int y0,int x1,int y1,float limit)const{
        const int size=int(std::sqrt(double(pyramid[level].size())));
        if(tx>=size||ty>=size)return false;
        const int span=1<<level,px0=tx*span,py0=ty*span,px1=px0+span-1,py1=py0+span-1;
        if(px1<x0||py1<y0||px0>x1||py0>y1)return false;
        if(pyramid[level][size_t(ty)*size+tx]>limit)return false;
        if(level==0||(px0>=x0&&py0>=y0&&px1<=x1&&py1<=y1))return true;
        for(int dy=0;dy<2;++dy)for(int dx=0;dx<2;++dx)
            if(anyAtOrBehind(level-1,tx*2+dx,ty*2+dy,x0,y0,x1,y1,limit))return true;
        return false;
    }
    bool boxVisible(const Box& b,V3 eye,const Face& f)const{
        V3 c[8];
        for(int i=0;i<8;++i){
            V3 d=sub({i&1?b.hi[0]:b.lo[0],i&2?b.hi[1]:b.lo[1],i&4?b.hi[2]:b.lo[2]},eye);
            c[i]={dot(d,f.r),dot(d,f.u),dot(d,f.f)};
        }
        const double k=p.k;
        auto all=[&](auto pred){for(const auto& q:c)if(!pred(q))return false;return true;};
        if(all([&](V3 q){return q.z<p.near;}))return false;
        if(all([&](V3 q){return q.x*k>q.z;})||all([&](V3 q){return -q.x*k>q.z;})||
           all([&](V3 q){return q.y*k>q.z;})||all([&](V3 q){return -q.y*k>q.z;}))return false;
        // The box clipped to z >= near: its corners in front plus the
        // crossings of its 12 edges with the near plane.
        static const int Edges[12][2]={{0,1},{2,3},{4,5},{6,7},{0,2},{1,3},{4,6},{5,7},{0,4},{1,5},{2,6},{3,7}};
        V3 pts[20];int n=0;double zMin=1e300;
        for(const auto& q:c)if(q.z>=p.near){pts[n++]=q;zMin=std::min(zMin,q.z);}
        for(const auto& e:Edges){
            const V3 a=c[e[0]],q=c[e[1]];
            if((a.z<p.near)!=(q.z<p.near)){const double s=(p.near-a.z)/(q.z-a.z);pts[n++]={a.x+(q.x-a.x)*s,a.y+(q.y-a.y)*s,p.near};zMin=p.near;}
        }
        const double half=.5*p.res;
        double x0=1e300,y0=1e300,x1=-1e300,y1=-1e300;
        for(int i=0;i<n;++i){
            const double w=1./pts[i].z,x=(pts[i].x*k*w+1.)*half,y=(pts[i].y*k*w+1.)*half;
            x0=std::min(x0,x);x1=std::max(x1,x);y0=std::min(y0,y);y1=std::max(y1,y);
        }
        const int ix0=std::max(0,int(std::floor(x0-1.))),ix1=std::min(int(p.res)-1,int(std::ceil(x1+1.)));
        const int iy0=std::max(0,int(std::floor(y0-1.))),iy1=std::min(int(p.res)-1,int(std::ceil(y1+1.)));
        if(ix0>ix1||iy0>iy1)return false;
        // Visible where the occluder is at or behind the box's nearest point
        // (same tolerance as the triangle test).
        const float limit=float((1./zMin)/(1.-1e-3));
        const int top=int(pyramid.size())-1;
        return anyAtOrBehind(top,0,0,ix0,iy0,ix1,iy1,limit);
    }
    void sample(V3 eye,uint8_t* out,uint8_t* boxOut){
        std::fill(seen.begin(),seen.end(),0);
        std::fill(boxSeen.begin(),boxSeen.end(),0);
        for(size_t i=0;i<boxes.size();++i){
            const Box& b=boxes[i];const double m=p.near;
            if(eye.x>=b.lo[0]-m&&eye.x<=b.hi[0]+m&&eye.y>=b.lo[1]-m&&eye.y<=b.hi[1]+m&&eye.z>=b.lo[2]-m&&eye.z<=b.hi[2]+m)boxSeen[i]=1;
        }
        V3 poly[4];
        for(const auto& f:Faces){
            std::fill(depth.begin(),depth.end(),0.f);
            for(uint32_t i:occluders){
                const Tri& t=tris[i];
                if(!frontFacing(t,eye))continue;
                int n=clip(t,eye,f,poly);
                for(int j=1;j+1<n;++j)raster(poly[0],poly[j],poly[j+1],0);
            }
            const int r=int(p.res);
            for(int y=0;y<r;++y)for(int x=0;x<r;++x){
                float m=depth[size_t(y)*r+x];
                for(int dy=-1;dy<=1;++dy)for(int dx=-1;dx<=1;++dx){
                    int yy=y+dy,xx=x+dx;
                    m=(yy<0||yy>=r||xx<0||xx>=r)?m:std::min(m,depth[size_t(yy)*r+xx]);
                }
                eroded[size_t(y)*r+x]=m;
            }
            for(const auto& t:tris){
                if(seen[t.block]||!frontFacing(t,eye))continue;
                int n=clip(t,eye,f,poly);
                for(int j=1;j+1<n;++j)if(raster(poly[0],poly[j],poly[j+1],1)){seen[t.block]=1;break;}
            }
            if(!boxes.empty()){
                buildPyramid();
                for(size_t i=0;i<boxes.size();++i)if(!boxSeen[i]&&boxVisible(boxes[i],eye,f))boxSeen[i]=1;
            }
        }
        for(size_t b=0;b<seen.size();++b)if(seen[b])out[b>>3]|=uint8_t(1u<<(b&7));
        for(size_t b=0;b<boxSeen.size();++b)if(boxSeen[b])boxOut[b>>3]|=uint8_t(1u<<(b&7));
    }
};
}

int main(int argc,char** argv){
    if(argc!=3){std::fprintf(stderr,"usage: pvs_raster INPUT OUTPUT\n");return 2;}
    FILE* in=std::fopen(argv[1],"rb");if(!in)return 1;
    char magic[4];uint32_t h[5];float nearPlane,fov;int32_t frontSign;
    if(std::fread(magic,1,4,in)!=4||std::memcmp(magic,"PVSI",4)||std::fread(h,4,5,in)!=5||(h[0]!=1&&h[0]!=2)||
       std::fread(&nearPlane,4,1,in)!=1||std::fread(&fov,4,1,in)!=1||std::fread(&frontSign,4,1,in)!=1)return 1;
    const uint32_t triCount=h[1],blocks=h[2],samples=h[3],res=h[4];
    std::vector<Tri> tris(triCount);std::vector<float> eyes(size_t(samples)*3);
    if(std::fread(tris.data(),sizeof(Tri),triCount,in)!=triCount||std::fread(eyes.data(),4,eyes.size(),in)!=eyes.size())return 1;
    std::vector<Box> boxes;
    if(h[0]==2){
        uint32_t boxCount;
        if(std::fread(&boxCount,4,1,in)!=1||boxCount>(1u<<20))return 1;
        boxes.resize(boxCount);
        if(std::fread(boxes.data(),sizeof(Box),boxCount,in)!=boxCount)return 1;
    }
    std::fclose(in);
    std::vector<uint32_t> occluders;
    for(uint32_t i=0;i<triCount;++i){if(tris[i].block>=blocks)return 1;if(tris[i].flags&1)occluders.push_back(i);}
    const Params params{res,nearPlane,1./std::tan(fov*.5*M_PI/180.),frontSign};
    const size_t blockBytes=(blocks+7)/8,stride=blockBytes+(boxes.size()+7)/8;
    std::vector<uint8_t> result(size_t(samples)*stride,0);
    std::atomic<uint32_t> next{0},done{0};
    unsigned threads=std::max(1u,std::thread::hardware_concurrency());
    std::vector<std::thread> pool;
    for(unsigned t=0;t<threads;++t)pool.emplace_back([&]{
        Worker w(params,tris,occluders,blocks,boxes);
        for(uint32_t s;(s=next++)<samples;){
            w.sample({eyes[s*3],eyes[s*3+1],eyes[s*3+2]},result.data()+size_t(s)*stride,result.data()+size_t(s)*stride+blockBytes);
            uint32_t d=++done;if(d%2000==0)std::fprintf(stderr,"  %u/%u samples\n",d,samples);
        }
    });
    for(auto& t:pool)t.join();
    FILE* out=std::fopen(argv[2],"wb");if(!out)return 1;
    std::fwrite(result.data(),1,result.size(),out);std::fclose(out);
    return 0;
}
